"""Run one attempt of each background AI job type."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.services.ai_jobs.supervision import run_detached
from src.app.services.chat_turn_orchestrator import (
    ChatTurnOrchestrator,
    PreparedChatTurn,
)
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobClaim
from src.domain.model.chat.models import ChatSseEvent
from src.domain.model.meal import MealStatus
from src.domain.model.meal_projection import MealProjection

logger = logging.getLogger(__name__)

Send = Callable[[Any], Awaitable[Any]]

CHAT_TERMINAL_EVENTS = frozenset({"message.completed", "message.error"})
_FINISHED_MEAL_STATUSES = frozenset({MealStatus.READY, MealStatus.INACTIVE})
_CLEANUP_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class AiJobInterrupt:
    """Why an attempt is being stopped early; set before it is cancelled."""

    code: str | None = None

    @property
    def chat_error_code(self) -> str:
        """Error stored on an interrupted chat reply."""
        return "CHAT_STOPPED" if self.code == "CHAT_STOPPED" else "CHAT_TURN_FAILED"


class AiJobExecutor(Protocol):
    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]: ...


class MealScanJobExecutor:
    """Analyzes an uploaded photo and saves the meal under the job's meal id.

    The id is minted at enqueue time, so an attempt that saved the meal but
    lost its lease before recording the result is not analyzed twice.
    """

    def __init__(self, *, send: Send, uow_factory: Callable[[], Any]) -> None:
        self._send = send
        self._uow_factory = uow_factory

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        payload = claim.request_payload
        meal_id = _required_str(payload, "meal_id")
        async with self._uow_factory() as uow:
            existing = await uow.meals.find_by_id(
                meal_id, projection=MealProjection.MACROS_ONLY
            )
        if existing is not None:
            if existing.user_id != claim.user_id:
                raise AiJobExecutionError("INVALID_JOB_INPUT", "meal id is taken")
            if existing.status in _FINISHED_MEAL_STATUSES:
                return {"meal_id": meal_id}
        meal = await self._send(
            ScanByUrlCommand(
                user_id=claim.user_id,
                image_url=_required_str(payload, "image_url"),
                public_id=_required_str(payload, "public_id"),
                user_description=_optional_str(payload, "user_description"),
                target_date=_optional_date(payload, "target_date"),
                language=claim.language,
                scan_mode=_optional_str(payload, "scan_mode") or "scanner",
                label_crop_image_url=_optional_str(payload, "label_crop_image_url"),
                label_crop_public_id=_optional_str(payload, "label_crop_public_id"),
                crop_metadata=_optional_dict(payload, "crop_metadata"),
                meal_id=meal_id,
            )
        )
        if getattr(meal, "status", None) == MealStatus.FAILED:
            raise AiJobExecutionError("FAILED_TO_ANALYZE_MEAL_IMAGE")
        return {"meal_id": str(getattr(meal, "meal_id", None) or meal_id)}


class ParseTextJobExecutor:
    """Parses a typed meal description; finding no foods is still a result."""

    def __init__(
        self, *, send: Send, to_result: Callable[[Any], dict[str, Any]]
    ) -> None:
        self._send = send
        self._to_result = to_result

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        payload = claim.request_payload
        current_items = payload.get("current_items")
        if current_items is not None and not isinstance(current_items, list):
            raise AiJobExecutionError("INVALID_JOB_INPUT", "current_items")
        response = await self._send(
            ParseMealTextCommand(
                text=_required_str(payload, "text"),
                language=claim.language,
                user_id=claim.user_id,
                current_items=current_items,
            )
        )
        return self._to_result(response)


class ChatJobExecutor:
    """Re-runs a chat turn nobody is streaming: an automatic retry, or a turn
    whose process went away. The orchestrator persists the reply."""

    def __init__(self, *, orchestrator_factory: Callable[[], ChatTurnOrchestrator]):
        self._orchestrator_factory = orchestrator_factory

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        payload = claim.request_payload
        function_args = payload.get("function_args")
        if function_args is not None and not isinstance(function_args, dict):
            raise AiJobExecutionError("INVALID_JOB_INPUT", "function_args")
        orchestrator = self._orchestrator_factory()
        prepared = await orchestrator.prepare_turn(
            user_id=claim.user_id,
            content=_required_str(payload, "content"),
            idempotency_key=_required_str(payload, "idempotency_key"),
            locale=_optional_str(payload, "locale"),
            header_timezone=_optional_str(payload, "header_timezone"),
            user_language=_optional_str(payload, "user_language"),
            intent=_optional_str(payload, "intent"),
            function_name=_optional_str(payload, "function_name"),
            function_args=function_args,
        )
        terminal = await run_chat_turn(
            orchestrator, user_id=claim.user_id, prepared=prepared, interrupt=interrupt
        )
        return chat_job_result(terminal)


async def run_chat_turn(
    orchestrator: ChatTurnOrchestrator,
    *,
    user_id: str,
    prepared: PreparedChatTurn,
    interrupt: AiJobInterrupt,
    on_event: Callable[[ChatSseEvent], None] | None = None,
) -> ChatSseEvent | None:
    """Stream a prepared turn to its terminal event.

    An interrupted turn fails its reply right away, so the user can resend
    it instead of waiting for the chat lease to run out.
    """
    try:
        return await drain_chat_turn(
            orchestrator.stream_prepared(user_id=user_id, prepared=prepared),
            on_event,
        )
    except asyncio.CancelledError:
        await run_detached(
            orchestrator.abandon_turn(prepared, error_code=interrupt.chat_error_code),
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            description="abandon chat turn",
        )
        raise


async def drain_chat_turn(
    stream: AsyncIterator[ChatSseEvent],
    on_event: Callable[[ChatSseEvent], None] | None = None,
) -> ChatSseEvent | None:
    """Consume every event so the stream's own cleanup runs; return the last
    terminal event."""
    terminal: ChatSseEvent | None = None
    try:
        async for event in stream:
            if event.event in CHAT_TERMINAL_EVENTS:
                terminal = event
            if on_event is not None:
                on_event(event)
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()
    return terminal


def chat_job_result(terminal: ChatSseEvent | None) -> dict[str, Any]:
    if terminal is None:
        raise AiJobExecutionError("CHAT_TURN_FAILED", "chat stream ended early")
    if terminal.event == "message.error":
        code = terminal.data.get("code")
        raise AiJobExecutionError(
            code if isinstance(code, str) and code else "CHAT_TURN_FAILED"
        )
    return {
        "thread_id": terminal.data.get("thread_id"),
        "assistant_message_id": terminal.data.get("assistant_message_id"),
    }


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"missing {key}")
    return value


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}")
    return value


def _optional_dict(payload: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}")
    return value


def _optional_date(payload: Mapping[str, Any], key: str) -> date | None:
    value = _optional_str(payload, key)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}") from exc

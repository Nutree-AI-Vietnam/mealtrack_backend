"""Authenticated single-thread Nutree coach API.

A message sent with ``ai_job_id`` also becomes a background AI job: the reply
keeps generating if the client goes away, and the job records the outcome.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.dependencies.ai_jobs import get_ai_job_service, get_chat_job_runner
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.chat import get_chat_turn_orchestrator
from src.api.exceptions import ValidationException
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.chat_requests import ChatMessageCreateRequest
from src.api.schemas.response.chat_responses import (
    ChatClearResponse,
    ChatThreadResponse,
)
from src.app.services.ai_jobs import AiJobService, ChatJobRunner, new_chat_job
from src.app.services.chat_turn_orchestrator import (
    ChatTurnOrchestrator,
    PreparedChatTurn,
)
from src.domain.exceptions.ai_job_exceptions import (
    AiJobConflictError,
    AiJobsUnavailableError,
)
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
    ChatRegenerateNotAllowedError,
)
from src.domain.model.ai_jobs import AiJobClaim
from src.domain.model.chat import ChatClaimKind, ChatSseEvent
from src.domain.services.chat.coach_functions import (
    preferred_alias,
    resolve_coach_function,
)
from src.infra.services.durable_write_service import normalize_idempotency_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["Chat"])


@router.get("", response_model=ChatThreadResponse)
async def get_chat_thread(
    request: Request,
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
    ai_jobs: AiJobService = Depends(get_ai_job_service),
) -> ChatThreadResponse:
    payload = await orchestrator.get_thread(
        user_id=user_id,
        limit=limit,
        before=before,
        locale=get_request_language(request),
        header_timezone=request.headers.get("X-Timezone"),
    )
    in_flight = payload.get("in_flight")
    if in_flight and in_flight.get("idempotency_key"):
        in_flight["ai_job_id"] = await ai_jobs.find_active_chat_job_id(
            user_id=user_id, dedupe_key=in_flight["idempotency_key"]
        )
    return ChatThreadResponse.model_validate(payload)


@router.delete("", response_model=ChatClearResponse)
async def clear_chat_thread(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatClearResponse:
    del request
    payload = await orchestrator.clear_thread(user_id)
    return ChatClearResponse.model_validate(payload)


@router.post("/messages", response_model=None)
@limiter.limit("10/minute")
async def post_chat_message(
    request: Request,
    payload: ChatMessageCreateRequest,
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
    ai_jobs: AiJobService = Depends(get_ai_job_service),
    runner: ChatJobRunner = Depends(get_chat_job_runner),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StreamingResponse | JSONResponse:
    try:
        idempotency_key = normalize_idempotency_key(idempotency_key_header)
    except ValueError as exc:
        raise ValidationException(
            str(exc), error_code="INVALID_IDEMPOTENCY_KEY"
        ) from exc
    if not idempotency_key:
        raise ValidationException(
            "Idempotency-Key is required",
            error_code="IDEMPOTENCY_KEY_REQUIRED",
        )

    locale = payload.locale or get_request_language(request)
    header_timezone = request.headers.get("X-Timezone")

    intent_value = payload.intent.value if payload.intent else None
    function_name: str | None = None
    function_args: dict | None = None
    if payload.tool is not None:
        resolved = resolve_coach_function(payload.tool.name, args=payload.tool.args)
        if resolved is None:
            raise ValidationException(
                f"Unknown tool '{payload.tool.name}'",
                error_code="CHAT_UNKNOWN_TOOL",
            )
        # Tool wins over intent when both are present.
        if intent_value is not None and intent_value != preferred_alias(
            resolved.name, resolved.args
        ):
            logger.info(
                "chat tool overrides intent",
                extra={
                    "tool": resolved.name,
                    "ignored_intent": intent_value,
                    "user_id": user_id,
                },
            )
        function_name = resolved.name
        function_args = resolved.args
        intent_value = preferred_alias(resolved.name, resolved.args)

    try:
        if payload.regenerate_assistant_id:
            prepared = await orchestrator.prepare_regeneration(
                user_id=user_id,
                assistant_message_id=payload.regenerate_assistant_id,
                content=payload.content,
                locale=locale,
                header_timezone=header_timezone,
                user_language=locale,
            )
        else:
            prepared = await orchestrator.prepare_turn(
                user_id=user_id,
                content=payload.content,
                idempotency_key=idempotency_key,
                locale=locale,
                header_timezone=header_timezone,
                user_language=locale,
                intent=intent_value,
                function_name=function_name,
                function_args=function_args,
            )
    except ChatRegenerateNotAllowedError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "CHAT_REGENERATE_NOT_ALLOWED",
            "Only the latest coach reply can be regenerated",
        )
    except ChatBusyError as exc:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "CHAT_BUSY",
            "A chat turn is already in progress",
            retry_after=exc.retry_after_seconds,
        )
    except ChatIdempotencyConflictError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "CHAT_IDEMPOTENCY_CONFLICT",
            "Idempotency key was reused with a different payload",
        )
    except ChatRateLimitedError as exc:
        return _error_response(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "CHAT_DAILY_LIMIT" if exc.daily else "CHAT_RATE_LIMITED",
            "Chat turn limit exceeded",
            retry_after=exc.retry_after_seconds,
        )
    except ChatProviderUnavailableError as exc:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "CHAT_PROVIDER_UNAVAILABLE",
            "Chat is temporarily unavailable",
            retry_after=exc.retry_after_seconds,
        )

    # A regenerated reply streams without a job: a job retry rebuilds its turn
    # with prepare_turn, which would ask the question again as a new turn.
    job_id = None if payload.regenerate_assistant_id else payload.ai_job_id
    if job_id is not None and prepared.claim.kind != ChatClaimKind.REPLAY:
        try:
            claim = await _start_chat_job(
                ai_jobs,
                prepared,
                job_id=str(job_id),
                user_id=user_id,
                content=payload.content,
                idempotency_key=idempotency_key,
                locale=locale,
                header_timezone=header_timezone,
                intent=intent_value,
                function_name=function_name,
                function_args=function_args,
            )
        except AiJobConflictError:
            await orchestrator.abandon_turn(prepared, error_code="CHAT_TURN_FAILED")
            return _error_response(
                status.HTTP_409_CONFLICT,
                "AI_JOB_CONFLICT",
                "This job id is already used by a different request",
            )
        if claim is not None:
            session = runner.start(
                orchestrator=orchestrator, claim=claim, prepared=prepared
            )

            async def job_stream() -> AsyncIterator[bytes]:
                async with aclosing(runner.stream(session)) as events:
                    async for event in events:
                        yield _encode_sse(event)

            return _sse_response(job_stream())

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async for event in orchestrator.stream_prepared(
                user_id=user_id,
                prepared=prepared,
            ):
                yield _encode_sse(event)
        finally:
            orchestrator.release_slot(prepared)

    return _sse_response(event_stream())


async def _start_chat_job(
    ai_jobs: AiJobService,
    prepared: PreparedChatTurn,
    *,
    job_id: str,
    user_id: str,
    content: str,
    idempotency_key: str,
    locale: str,
    header_timezone: str | None,
    intent: str | None,
    function_name: str | None,
    function_args: dict | None,
) -> AiJobClaim | None:
    """Claim the turn's job; None streams the turn without one.

    Raises AiJobConflictError when the job id belongs to another request.
    """
    job = new_chat_job(
        job_id=job_id,
        user_id=user_id,
        language=prepared.locale,
        content=content,
        idempotency_key=idempotency_key,
        # Stored exactly as passed to prepare_turn, so a retry rebuilds the
        # same turn.
        locale=locale,
        header_timezone=header_timezone,
        user_language=locale,
        intent=intent,
        function_name=function_name,
        function_args=function_args,
    )
    try:
        return await ai_jobs.start_chat_job(job)
    except AiJobConflictError:
        raise
    except AiJobsUnavailableError:
        return None
    except Exception:
        logger.warning("Chat turn streams without an AI job", exc_info=True)
        return None


def _sse_response(body: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _encode_sse(event: ChatSseEvent) -> bytes:
    payload = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"event: {event.event}\ndata: {payload}\n\n".encode()


def _error_response(
    status_code: int,
    error_code: str,
    message: str,
    *,
    retry_after: int | None = None,
) -> JSONResponse:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {
                "error_code": error_code,
                "message": message,
                "details": {},
            }
        },
        headers=headers,
    )

"""Run a Ghi nhanh sentence the app handed off as a background AI job.

The job logs the sentence exactly like its synchronous route (water,
activity, or the + panel) and stores that route's reply as its result. The
route's guard (switch, Premium, daily quota, sentence) ran when the job was
queued, so the worker never spends quota or meets the paywall.

The job id is the sentence's replay key. A retry after a lost lease
replays the saved reply instead of logging the sentence twice, and the
+ panel's undo (``DELETE /v1/log/describe/{job_id}``) finds it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, time
from typing import Any

from src.app.commands.describe_any import DescribeAnyCommand
from src.app.commands.hydration import DescribeHydrationCommand
from src.app.commands.movement import DescribeMovementCommand
from src.app.services.ai_jobs.executors import AiJobInterrupt, Send
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobClaim, AiJobType
from src.domain.utils.fingerprint_utils import canonicalize_fingerprint

ToResult = Callable[[Any], dict[str, Any]]

_DESCRIBE_KINDS = frozenset({"food", "drink", "activity", "supplement"})


class DescribeJobExecutor:
    """One executor for the three describe job types."""

    def __init__(self, *, send: Send, to_result: Mapping[AiJobType, ToResult]):
        self._send = send
        self._to_result = to_result

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        command = self._command(claim)
        reply = await self._send(command)
        result = self._to_result[claim.job_type](reply)
        if claim.job_type == AiJobType.DESCRIBE_LOG and not result.get("saved"):
            # The + panel answers this with a 200; a job reports it as a
            # failure, so its push does not say something was logged.
            raise AiJobExecutionError("DESCRIBE_NOTHING_RECOGNIZED")
        return result

    def _command(self, claim: AiJobClaim) -> Any:
        payload = claim.request_payload
        text = _required_str(payload, "text")
        timezone = _optional_str(payload, "header_timezone")
        key = claim.id
        if claim.job_type == AiJobType.DESCRIBE_MOVEMENT:
            target_date = _optional_str(payload, "target_date")
            return DescribeMovementCommand(
                user_id=claim.user_id,
                text=text,
                target_date=_date(target_date),
                weight_kg=_optional_float(payload, "weight_kg"),
                header_timezone=timezone,
                idempotency_key=key,
                request_fingerprint=canonicalize_fingerprint(
                    {"text": text, "target_date": target_date}
                ),
            )
        if claim.job_type == AiJobType.DESCRIBE_HYDRATION:
            target_date = _optional_str(payload, "target_date")
            return DescribeHydrationCommand(
                user_id=claim.user_id,
                text=text,
                target_date=_date(target_date),
                header_timezone=timezone,
                language=claim.language,
                idempotency_key=key,
                request_fingerprint=canonicalize_fingerprint(
                    {"text": text, "target_date": target_date}
                ),
            )
        if claim.job_type == AiJobType.DESCRIBE_LOG:
            local_date = _optional_str(payload, "local_date")
            kinds = _kinds(payload)
            return DescribeAnyCommand(
                user_id=claim.user_id,
                text=text,
                kinds=kinds,
                target_date=_date(local_date),
                local_time=_time(_optional_str(payload, "local_time")),
                header_timezone=timezone,
                language=claim.language,
                idempotency_key=key,
                request_fingerprint=canonicalize_fingerprint(
                    {"text": text, "local_date": local_date, "kinds": sorted(kinds)}
                ),
            )
        raise AiJobExecutionError(
            "INVALID_JOB_INPUT", f"not a describe job: {claim.job_type}"
        )


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


def _optional_float(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}")
    return float(value)


def _kinds(payload: Mapping[str, Any]) -> frozenset[str]:
    value = payload.get("kinds")
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(kind, str) and kind in _DESCRIBE_KINDS for kind in value)
    ):
        raise AiJobExecutionError("INVALID_JOB_INPUT", "invalid kinds")
    return frozenset(value)


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AiJobExecutionError("INVALID_JOB_INPUT", "invalid date") from exc


def _time(value: str | None) -> time | None:
    if value is None:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise AiJobExecutionError("INVALID_JOB_INPUT", "invalid local_time") from exc

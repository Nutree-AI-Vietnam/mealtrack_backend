"""Run one attempt at writing a weekly plan's dishes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from typing import Any

from src.app.commands.meal_planner.write_weekly_plan_dishes_command import (
    WriteWeeklyPlanDishesCommand,
)
from src.app.services.ai_jobs.executors import AiJobInterrupt
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobClaim

Send = Callable[[Any], Awaitable[Any]]


class WeeklyPlanJobExecutor:
    """Writes the dishes of a plan already waiting in 'preparing'.

    The plan id is minted before the job is queued, so an attempt that
    stored the week but lost its lease is not written twice: the retry
    finds the plan out of 'preparing' and the slots already filled.
    """

    def __init__(self, *, send: Send) -> None:
        self._send = send

    async def execute(
        self, claim: AiJobClaim, interrupt: AiJobInterrupt
    ) -> dict[str, Any]:
        payload = claim.request_payload
        return await self._send(
            WriteWeeklyPlanDishesCommand(
                user_id=claim.user_id,
                plan_id=_text(payload, "plan_id"),
                week_start_date=_day(payload, "week_start_date"),
                daily_calories=_whole(payload, "daily_calories"),
                timezone=_optional_text(payload, "timezone"),
                language=claim.language,
                daily_protein=_optional_number(payload, "daily_protein"),
                daily_carbs=_optional_number(payload, "daily_carbs"),
                daily_fat=_optional_number(payload, "daily_fat"),
            )
        )


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}")
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _day(payload: Mapping[str, Any], key: str) -> date:
    try:
        return date.fromisoformat(_text(payload, key))
    except ValueError as error:
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}") from error


def _whole(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise AiJobExecutionError("INVALID_JOB_INPUT", f"invalid {key}")
    return int(value)


def _optional_number(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)

"""Create or replace an upcoming vacation."""

import logging
import uuid
from datetime import date
from typing import Any

from src.api.exceptions import ValidationException
from src.app.commands.vacation.set_vacation_command import SetVacationCommand
from src.app.events.base import EventHandler, handles
from src.app.services.vacation_support import (
    invalidate_vacation_views,
    vacation_payload,
)
from src.domain.model.vacation.vacation import Vacation
from src.domain.ports.cache_port import CachePort
from src.domain.services.vacation_rules import open_vacation, status_for, to_window
from src.domain.utils.timezone_utils import (
    resolve_user_timezone_async,
    user_today,
    utc_now,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(SetVacationCommand)
class SetVacationCommandHandler(EventHandler[SetVacationCommand, dict[str, Any]]):
    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, command: SetVacationCommand) -> dict[str, Any]:
        if command.end_date < command.start_date:
            raise ValidationException(
                message="The last day has to be on or after the first day.",
                error_code="VACATION_RANGE_INVALID",
            )

        async with AsyncUnitOfWork() as uow:
            user_tz = await resolve_user_timezone_async(command.user_id, uow)
            today = user_today(user_tz)
            if command.start_date < today:
                raise ValidationException(
                    message="A vacation cannot start in the past.",
                    error_code="VACATION_START_IN_PAST",
                )

            existing = await uow.vacations.find_by_user(command.user_id)
            current = open_vacation(existing, today)
            if current is not None and status_for(to_window(current), today) == "active":
                raise ValidationException(
                    message="This vacation is already on. Extend it or end it.",
                    error_code="VACATION_ALREADY_ACTIVE",
                )

            frozen = await _frozen_targets(uow, command.user_id, today)
            if current is None:
                vacation = Vacation(
                    vacation_id=str(uuid.uuid4()),
                    user_id=command.user_id,
                    start_date=command.start_date,
                    end_date=command.end_date,
                    ended_on=None,
                    frozen_calories=frozen[0],
                    frozen_protein=frozen[1],
                    frozen_carbs=frozen[2],
                    frozen_fat=frozen[3],
                    created_at=utc_now(),
                )
            else:
                current.start_date = command.start_date
                current.end_date = command.end_date
                current.ended_on = None
                current.frozen_calories = frozen[0]
                current.frozen_protein = frozen[1]
                current.frozen_carbs = frozen[2]
                current.frozen_fat = frozen[3]
                vacation = current

            await uow.vacations.save(vacation)
            await uow.commit()

        await invalidate_vacation_views(self.cache_service, command.user_id)
        logger.info(
            "Set vacation user_id=%s start=%s end=%s",
            command.user_id,
            command.start_date.isoformat(),
            command.end_date.isoformat(),
        )
        return {"vacation": vacation_payload(vacation, today)}


async def _frozen_targets(
    uow: AsyncUnitOfWork,
    user_id: str,
    today: date,
) -> tuple[float, float, float, float]:
    from src.app.handlers.query_handlers.get_daily_macros_query_handler import (
        GetDailyMacrosQueryHandler,
    )
    from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
        GetUserTdeeQueryHandler,
    )
    from src.app.queries.tdee import GetUserTdeeQuery
    from src.domain.model.user import MacroPreset
    from src.domain.services.weekly_budget_service import WeeklyBudgetService
    from src.domain.utils.timezone_utils import get_user_monday_async

    try:
        result = await GetUserTdeeQueryHandler().handle(
            GetUserTdeeQuery(user_id=user_id)
        )
    except Exception as exc:
        logger.warning("Could not freeze targets for %s: %s", user_id, exc)
        raise ValidationException(
            message="Targets are not ready to freeze yet.",
            error_code="VACATION_TARGETS_UNAVAILABLE",
        ) from exc

    macros = result.get("macros") or {}
    calories = float(result.get("target_calories") or 0)
    if calories <= 0:
        raise ValidationException(
            message="Targets are not ready to freeze yet.",
            error_code="VACATION_TARGETS_UNAVAILABLE",
        )
    base = (
        calories,
        float(macros.get("protein") or 0),
        float(macros.get("carbs") or 0),
        float(macros.get("fat") or 0),
    )
    try:
        preset_name = str(result.get("macro_preset") or MacroPreset.STANDARD.value)
        macro_preset = MacroPreset(preset_name)
    except ValueError:
        macro_preset = MacroPreset.STANDARD
    try:
        user_tz = await resolve_user_timezone_async(user_id, uow)
        week_start = await get_user_monday_async(today, user_id, uow)
        weekly_budget = await uow.weekly_budgets.find_by_user_and_week(
            user_id, week_start
        )
        auto_adjust = WeeklyBudgetService.auto_adjust_enabled(
            await uow.users.get_weekly_auto_adjust(user_id)
        )
        context = await GetDailyMacrosQueryHandler()._get_weekly_context(
            uow,
            user_id,
            today,
            weekly_budget,
            calories,
            macros,
            0,
            bmr=float(result.get("bmr") or 0),
            user_timezone=user_tz,
            macro_preset=macro_preset,
            is_custom=bool(result.get("is_custom")),
            target_revision=result.get("target_revision"),
            auto_adjust=auto_adjust,
        )
    except Exception as exc:
        logger.warning("Freezing base targets for %s: %s", user_id, exc)
        return base
    if not context:
        return base
    adjusted = float(context.get("adjusted_target_calories") or 0)
    if adjusted <= 0:
        return base
    return (
        adjusted,
        float(context.get("daily_protein") or base[1]),
        float(context.get("adjusted_target_carbs") or base[2]),
        float(context.get("adjusted_target_fat") or base[3]),
    )

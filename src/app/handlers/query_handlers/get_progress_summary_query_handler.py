"""GET /v1/progress/summary — per-day macros, targets, burn, hydration."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.handlers.query_handlers.progress_summary_rows import build_progress_day_row
from src.app.handlers.query_handlers.progress_summary_support import (
    fetch_movement_by_day,
    hydration_goal_ml,
    load_tdee_targets,
    local_meal_date,
    overlay_live_hydration,
    read_summary_cache,
    resolve_day_target,
    resolve_today_live_target,
    write_summary_cache,
)
from src.app.handlers.query_handlers.progress_summary_trends import (
    fetch_movement_entries_by_day,
    included_burn_by_day,
    trends_goal_fields,
)
from src.app.queries.progress.get_progress_summary_query import GetProgressSummaryQuery
from src.app.services.supplement_days import doses_between
from src.domain.model.meal import MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.cache_port import CachePort
from src.domain.services.hydration_intake import orphan_drink_day_totals
from src.domain.services.micronutrient_dri_targets import fiber_goal_g
from src.domain.services.progress_summary_window import (
    PROGRESS_SUMMARY_CAP_DAYS,
    clamp_progress_window,
)
from src.domain.services.weekly_budget_service import WeeklyBudgetService
from src.domain.utils.timezone_utils import get_zone_info, resolve_user_timezone_async
from src.infra.database.uow_async import AsyncUnitOfWork

_MEAL_RANGE_LIMIT = 10_000


@handles(GetProgressSummaryQuery)
class GetProgressSummaryQueryHandler(
    EventHandler[GetProgressSummaryQuery, dict[str, Any]]
):
    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, query: GetProgressSummaryQuery) -> dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            user_tz = get_zone_info(user_tz_str)
            today = datetime.now(user_tz).date()
            user = await uow.users.find_by_id(UUID(query.user_id))
            if user is None:
                raise ResourceNotFoundException("User not found")
            created_at = user.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            created_on = created_at.astimezone(user_tz).date()
            start, end = clamp_progress_window(
                query.start_date, query.end_date, created_on=created_on, today=today
            )

        # TDEE opens its own session. Close this one first so a summary
        # request never holds a connection while waiting for a second checkout.
        targets = await load_tdee_targets(query.user_id, self.cache_service)

        async with AsyncUnitOfWork() as uow:
            auto_adjust = WeeklyBudgetService.auto_adjust_enabled(
                await uow.users.get_weekly_auto_adjust(query.user_id)
            )
            activity_calories = WeeklyBudgetService.activity_calories_enabled(
                await uow.users.get_activity_calories(query.user_id)
            )
            cached, intake_revision = await read_summary_cache(
                self.cache_service,
                query.user_id,
                start,
                end,
                targets[4],
                auto_adjust,
                include_trends=query.include_trends,
                activity_calories=activity_calories,
            )
            if cached is not None:
                return await overlay_live_hydration(
                    uow, query.user_id, start, end, user_tz_str, cached
                )
            result = await self._compute(
                uow,
                query.user_id,
                start,
                end,
                today,
                user_tz_str,
                user_tz,
                targets,
                auto_adjust,
                include_trends=query.include_trends,
                activity_calories=activity_calories,
            )
            await write_summary_cache(
                self.cache_service,
                query.user_id,
                start,
                end,
                result,
                intake_revision,
                include_trends=query.include_trends,
            )
            return result

    async def _compute(
        self,
        uow: Any,
        user_id: str,
        start: date,
        end: date,
        today: date,
        user_tz_str: str,
        user_tz: Any,
        targets: tuple,
        auto_adjust: bool = True,
        *,
        include_trends: bool = False,
        activity_calories: bool = True,
    ) -> dict[str, Any]:
        base_cal, protein_t, _c, _f, revision, bmr = targets
        # Only the Trends fields use food items, whose micros fill the
        # nutrients the meal-level totals leave out. The day's score keeps
        # meal-level micros either way (see build_progress_day_row).
        meals = await uow.meals.find_by_date_range(
            user_id,
            start,
            end,
            limit=_MEAL_RANGE_LIMIT,
            user_timezone=user_tz_str,
            projection=MealProjection.MACROS_WITH_MICROS
            if include_trends
            else MealProjection.MACROS_ONLY,
        )
        meals_by_day: dict[date, list] = {}
        for meal in meals:
            if meal.status == MealStatus.INACTIVE:
                continue
            meal_day = local_meal_date(meal.created_at, user_tz)
            if meal_day:
                meals_by_day.setdefault(meal_day, []).append(meal)

        hydration_by_day = await uow.hydration_entries.sum_ml_by_date_range(
            user_id, start, end, user_timezone=user_tz_str
        )
        hydration_entries = await uow.hydration_entries.find_by_date_range(
            user_id, start, end, user_timezone=user_tz_str
        )
        if not isinstance(hydration_entries, list):
            hydration_entries = []
        drink_macros_by_day, drink_micros_by_day = orphan_drink_day_totals(
            hydration_entries, meals_by_day, user_tz_str
        )
        entries_by_day: dict[date, list[Any]] = {}
        # Doses count in the day highlights on both paths, as on Home.
        doses_by_day: dict[date, list[Any]] = {}
        for dose in await doses_between(uow, user_id, start, end):
            doses_by_day.setdefault(dose.local_date, []).append(dose)
        if include_trends:
            entries_by_day = await fetch_movement_entries_by_day(
                uow, user_id, start, end, user_tz_str, user_tz
            )
            movement_by_day = included_burn_by_day(entries_by_day)
        else:
            movement_by_day = await fetch_movement_by_day(
                uow, user_id, start, end, user_tz_str, user_tz
            )
        cheat_days = await uow.cheat_days.find_by_user_and_date_range(
            user_id, start, end
        )
        snapshots = await uow.daily_target_snapshots.find_calories_by_range(
            user_id, start, end
        )
        profile = await uow.users.get_profile(UUID(user_id))
        live_cal = await resolve_today_live_target(
            uow,
            user_id,
            today,
            start,
            end,
            user_tz_str,
            base_cal,
            protein_t,
            bmr,
            activity_calories=activity_calories,
        )
        goal_ml = hydration_goal_ml(profile)
        fiber_target = fiber_goal_g(
            getattr(profile, "gender", None), getattr(profile, "age", None)
        )
        cheat_set = {cd.date for cd in cheat_days}

        days: list[dict[str, Any]] = []
        current = start
        while current <= end:
            source, target_cal = resolve_day_target(
                current,
                today,
                live_cal,
                snapshots,
                base_cal,
                auto_adjust,
            )
            days.append(
                build_progress_day_row(
                    current,
                    meals=meals_by_day.get(current, []),
                    burned_calories=movement_by_day.get(current, 0.0),
                    hydration_ml=hydration_by_day.get(current, 0),
                    hydration_goal_ml=goal_ml,
                    protein_target_g=protein_t,
                    fiber_target_g=fiber_target,
                    target_calories=target_cal,
                    target_source=source,
                    is_cheat_day=current in cheat_set,
                    drink_macros=drink_macros_by_day.get(current),
                    drink_micros=drink_micros_by_day.get(current),
                    trends_movement=entries_by_day.get(current, [])
                    if include_trends
                    else None,
                    supplement_doses=doses_by_day.get(current, []),
                )
            )
            current += timedelta(days=1)

        result: dict[str, Any] = {
            "effective_start": start.isoformat(),
            "effective_end": end.isoformat(),
            "cap_days": PROGRESS_SUMMARY_CAP_DAYS,
            "days": days,
            "target_revision": revision,
            "weekly_auto_adjust": auto_adjust,
            "activity_calories": activity_calories,
        }
        if include_trends:
            result.update(trends_goal_fields(profile, base_cal, revision))
        return result

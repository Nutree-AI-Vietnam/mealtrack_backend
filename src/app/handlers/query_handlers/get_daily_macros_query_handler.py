"""
GetDailyMacrosQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from src.app.events.base import EventHandler, handles
from src.app.queries.meal import GetDailyMacrosQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.meal import MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.model.nutrition.macros import Macros
from src.domain.model.user import MacroPreset, MacroTargets
from src.domain.ports.cache_port import CachePort
from src.domain.services.hydration_goal_service import resolve_hydration_goal_ml
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.services.tdee_service import TdeeCalculationService
from src.domain.services.weekly_budget_service import WeeklyBudgetService
from src.domain.utils.timezone_utils import (
    get_user_monday,
    get_zone_info,
    resolve_user_timezone_async,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetDailyMacrosQuery)
class GetDailyMacrosQueryHandler(EventHandler[GetDailyMacrosQuery, dict[str, Any]]):
    """Handler for calculating daily macronutrient totals with user targets."""

    def __init__(
        self,
        cache_service: CachePort | None = None,
    ):
        self.cache_service = cache_service

    async def handle(self, query: GetDailyMacrosQuery) -> dict[str, Any]:
        """Calculate daily macros for a given date with user targets."""
        # TDEE lookup FIRST — behind Redis cache, rarely opens its own DB
        # connection. Resolving it before the UoW below lets that single UoW
        # also run the weekly effective-adjusted call (previously a second,
        # separate AsyncUnitOfWork opened later in _get_weekly_context).
        target_calories = None
        target_macros = None
        bmr = 1800
        target_revision = None
        macro_preset = MacroPreset.STANDARD
        is_custom = False

        try:
            from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
                GetUserTdeeQueryHandler,
            )
            from src.app.queries.tdee import GetUserTdeeQuery

            tdee_handler = GetUserTdeeQueryHandler(cache_service=self.cache_service)
            tdee_result = await tdee_handler.handle(
                GetUserTdeeQuery(user_id=query.user_id)
            )
            target_calories = tdee_result.get("target_calories")
            target_macros = tdee_result.get("macros", {})
            bmr = tdee_result.get("bmr", 1800)
            target_revision = tdee_result.get("profile_target_revision")
            macro_preset = MacroPreset(tdee_result.get("macro_preset", "standard"))
            is_custom = bool(tdee_result.get("is_custom"))

            if target_calories is None:
                logger.warning(f"TDEE data missing for user {query.user_id}.")
        except Exception as e:
            logger.warning(
                f"Could not fetch TDEE data for user {query.user_id}: {e}",
                exc_info=True,
            )

        # One UoW for all DB reads: timezone, meals, weekly budget, and (when
        # a calorie target resolved above) the weekly effective-adjusted call.
        weekly_context: dict[str, Any] | None = None
        async with AsyncUnitOfWork() as uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            user_tz = get_zone_info(user_tz_str)
            target_date = query.target_date or datetime.now(user_tz).date()
            auto_adjust = WeeklyBudgetService.auto_adjust_enabled(
                await uow.users.get_weekly_auto_adjust(query.user_id)
            )

            # Cache-aside BEFORE meal aggregation. Returning a Redis hit after
            # computing fresh totals discarded those totals and could leave
            # clients with stale consumed=0 while meals already existed.
            cached_result = await self._try_get_cached_result(
                query.user_id, target_date, target_revision, auto_adjust
            )
            if cached_result is not None:
                return cached_result

            meals = await uow.meals.find_by_date(
                target_date,
                user_id=query.user_id,
                user_timezone=user_tz_str,
                projection=MealProjection.MACROS_ONLY,
            )

            total_protein = 0.0
            total_carbs = 0.0
            total_fat = 0.0
            total_calories = 0.0
            meal_count = 0
            meals_with_nutrition = 0
            has_legacy_hydration = False

            for meal in meals:
                if meal.status == MealStatus.INACTIVE:
                    continue
                if meal.meal_type == "hydration":
                    has_legacy_hydration = True
                meal_count += 1
                if meal.nutrition and meal.status in [
                    MealStatus.READY,
                    MealStatus.ENRICHING,
                ]:
                    meals_with_nutrition += 1
                    if meal.nutrition.macros:
                        total_protein += meal.nutrition.macros.protein or 0
                        total_carbs += meal.nutrition.macros.carbs or 0
                        total_fat += meal.nutrition.macros.fat or 0
                        total_calories += effective_meal_calories(meal)

            if not has_legacy_hydration:
                hydration_entries = await uow.hydration_entries.find_by_date(
                    target_date,
                    user_id=query.user_id,
                    user_timezone=user_tz_str,
                )
                for entry in hydration_entries:
                    meal_count += 1
                    meals_with_nutrition += 1
                    total_protein += entry.protein_g or 0
                    total_carbs += entry.carbs_g or 0
                    total_fat += entry.fat_g or 0
                    total_calories += Macros(
                        protein=entry.protein_g or 0,
                        carbs=entry.carbs_g or 0,
                        fat=entry.fat_g or 0,
                        fiber=entry.fiber_g or 0,
                    ).total_calories

            # Pre-fetch weekly budget eagerly here (must be inside this UoW — fetching it
            # later would require a second connection open). Only consumed when target_calories
            # is available; cheap indexed lookup so the overhead is negligible.
            week_start = get_user_monday(target_date, query.user_id)
            weekly_budget = await uow.weekly_budgets.find_by_user_and_week(
                query.user_id, week_start
            )

            # Fetch hydration summary
            try:
                consumed_water_ml = await uow.hydration_entries.sum_ml_for_date(
                    date_obj=target_date,
                    user_id=query.user_id,
                    user_timezone=user_tz_str,
                )
                if consumed_water_ml == 0:
                    consumed_water_ml = await uow.meals.sum_hydration_ml_for_date(
                        date_obj=target_date,
                        user_id=query.user_id,
                        user_timezone=user_tz_str,
                    )
                user_profile = await uow.users.get_profile(UUID(query.user_id))
                water_goal_ml = (
                    resolve_hydration_goal_ml(user_profile) if user_profile else 2000
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch hydration data for user %s: %s", query.user_id, exc
                )
                consumed_water_ml = 0
                water_goal_ml = 2000

            # Fetch movement kcal burned for this day
            movement_kcal_burned = 0.0
            try:
                from datetime import time

                start_local = datetime.combine(target_date, time.min, tzinfo=user_tz)
                end_local = start_local + timedelta(days=1)
                movement_kcal_burned = (
                    await uow.movement_entries.sum_included_kcal_for_range(
                        query.user_id,
                        start_local.astimezone(timezone.utc),  # noqa: UP017
                        end_local.astimezone(timezone.utc),  # noqa: UP017
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch movement data for user %s: %s", query.user_id, exc
                )

            food_calories = total_calories
            net_calories = food_calories - movement_kcal_burned

            if target_calories:
                weekly_context = await self._get_weekly_context(
                    uow,
                    query.user_id,
                    target_date,
                    weekly_budget,  # pre-fetched above
                    target_calories,
                    target_macros,
                    net_calories,
                    bmr,
                    user_tz_str,
                    macro_preset,
                    is_custom,
                    target_revision,
                    auto_adjust,
                )

        result = {
            "date": target_date.isoformat(),
            "user_id": query.user_id,
            "total_calories": round(net_calories, 1),
            "food_calories": round(food_calories, 1),
            "movement_kcal_burned": round(movement_kcal_burned, 1),
            "total_protein": round(total_protein, 1),
            "total_carbs": round(total_carbs, 1),
            "total_fat": round(total_fat, 1),
            "meal_count": meal_count,
            "meals_with_nutrition": meals_with_nutrition,
            "weekly_auto_adjust": auto_adjust,
        }

        if target_calories is not None:
            result["target_calories"] = target_calories
            result["profile_target_revision"] = target_revision
            result["target_revision"] = target_revision
            result["macro_preset"] = macro_preset.value

        if target_macros:
            result["target_macros"] = {
                "protein": target_macros.get("protein", 0.0),
                "carbs": target_macros.get("carbs", 0.0),
                "fat": target_macros.get("fat", 0.0),
                "calories": target_macros.get("calories", target_calories or 0.0),
            }

        if weekly_context:
            result["weekly_context"] = weekly_context

        result["hydration"] = {
            "consumed_ml": consumed_water_ml,
            "goal_ml": water_goal_ml,
            "percentage": (
                min(100.0, round(consumed_water_ml / water_goal_ml * 100, 1))
                if water_goal_ml > 0
                else 0.0
            ),
        }

        await self._write_cache(query.user_id, target_date, result)
        return result

    async def _get_weekly_context(
        self,
        uow,  # caller's open AsyncUnitOfWork — this method never opens its own
        user_id: str,
        target_date: date,
        weekly_budget,  # pre-fetched by caller; None → returns None
        target_calories: float,
        target_macros: dict,
        daily_consumed: float,
        bmr: float = 1800,
        user_timezone: str = "UTC",
        macro_preset: MacroPreset = MacroPreset.STANDARD,
        is_custom: bool = False,
        target_revision: int | None = None,
        auto_adjust: bool = True,
    ) -> dict[str, Any] | None:
        """Get weekly budget context using the weekly-budget adjustment path.

        Uses the caller's meal-aggregation UoW so the whole handler opens a
        single AsyncUnitOfWork per request.
        """
        if not weekly_budget:
            return None
        if target_revision is None or weekly_budget.target_revision != target_revision:
            logger.warning("Refusing stale weekly target row for user %s", user_id)
            return None
        try:
            week_start = get_user_monday(target_date, user_id)

            standard_daily_calories = target_calories
            standard_daily_protein = (
                target_macros.get("protein", 70) if target_macros else 70
            )
            standard_daily_carbs = (
                target_macros.get("carbs", 200) if target_macros else 200
            )
            standard_daily_fat = target_macros.get("fat", 70) if target_macros else 70

            effective = await WeeklyBudgetService.get_effective_adjusted_daily_async(
                uow=uow,
                user_id=user_id,
                week_start=week_start,
                target_date=target_date,
                weekly_budget=weekly_budget,
                base_daily_cal=standard_daily_calories,
                base_daily_protein=standard_daily_protein,
                base_daily_carbs=standard_daily_carbs,
                base_daily_fat=standard_daily_fat,
                bmr=bmr,
                user_timezone=user_timezone,
                auto_adjust=auto_adjust,
            )
            adjusted = effective.adjusted
            policy_targets = TdeeCalculationService.apply_adjusted_macro_policy(
                adjusted.calories,
                MacroTargets(
                    calories=adjusted.calories,
                    protein=adjusted.protein,
                    carbs=adjusted.carbs,
                    fat=adjusted.fat,
                ),
                macro_preset,
                is_custom,
            )

            return {
                "adjusted_target_calories": policy_targets.calories,
                "adjusted_target_carbs": policy_targets.carbs,
                "adjusted_target_fat": policy_targets.fat,
                "daily_protein": policy_targets.protein,
                "bmr_floor_active": adjusted.bmr_floor_active,
                "remaining_days": adjusted.remaining_days,
            }
        except Exception as e:
            logger.warning(f"Could not fetch weekly budget context: {e}")
            return None

    async def _try_get_cached_result(
        self,
        user_id: str,
        target_date: date,
        revision: int | None,
        auto_adjust: bool,
    ):
        if not self.cache_service:
            return None
        cache_key, _ = CacheKeys.daily_macros(user_id, target_date)
        try:
            cached = await self.cache_service.get_json(cache_key)
            if (
                revision is not None
                and cached
                and cached.get("target_revision") == revision
                and WeeklyBudgetService.auto_adjust_enabled(
                    cached.get("weekly_auto_adjust", True)
                )
                == auto_adjust
            ):
                return cached
            return None
        except Exception as exc:
            logger.warning("Failed to read daily macros cache for %s: %s", user_id, exc)
            return None

    async def _write_cache(
        self, user_id: str, target_date: date, payload: dict[str, Any]
    ):
        if not self.cache_service:
            return
        cache_key, ttl = CacheKeys.daily_macros(user_id, target_date)
        try:
            await self.cache_service.set_json(
                cache_key, payload, ttl, revision_field="target_revision"
            )
        except Exception as exc:
            logger.warning(
                "Failed to write daily macros cache for %s: %s", user_id, exc
            )

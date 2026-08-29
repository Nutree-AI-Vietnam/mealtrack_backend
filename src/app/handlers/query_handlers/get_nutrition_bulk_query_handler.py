"""
Handler for bulk nutrition query - returns date-indexed summaries for a range.
"""

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.nutrition import GetNutritionBulkQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.meal import MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.model.nutrition.macros import Macros
from src.domain.model.user import MacroPreset, MacroTargets
from src.domain.ports.cache_port import CachePort
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.services.tdee_service import TdeeCalculationService
from src.domain.services.weekly_budget_service import WeeklyBudgetService
from src.domain.utils.timezone_utils import (
    ensure_utc,
    get_user_monday,
    get_zone_info,
    resolve_user_timezone_async,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)

MAX_DATE_RANGE = 60


@handles(GetNutritionBulkQuery)
class GetNutritionBulkQueryHandler(EventHandler[GetNutritionBulkQuery, dict[str, Any]]):
    """Handler for bulk nutrition data retrieval."""

    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, query: GetNutritionBulkQuery) -> dict[str, Any]:
        """Fetch nutrition summaries for all dates in range (cache-aside).

        The full response is cached under a short-TTL per-range key. The short
        TTL bounds staleness from day-rollover (the weekly summary is relative
        to "today") and from rare target changes not on the high-frequency
        write paths; meal/movement/hydration/custom-macro writes purge this key
        synchronously (CacheInvalidationService) for instant freshness.
        """
        if (query.end_date - query.start_date).days > MAX_DATE_RANGE:
            raise ValueError(f"Date range cannot exceed {MAX_DATE_RANGE} days")

        if self.cache_service is None:
            return await self._compute(query)

        key, ttl = CacheKeys.nutrition_bulk(
            query.user_id, query.start_date, query.end_date
        )
        # A target-bearing cache is valid only for the current DB profile fence.
        user_targets = await self._get_user_targets(query.user_id)
        _, _, _, revision, _, _ = user_targets
        cached = await self.cache_service.get_json(key)
        if (
            revision is not None
            and cached
            and cached.get("target_revision") == revision
        ):
            return cached
        result = await self._compute(query, user_targets=user_targets)
        await self.cache_service.set_json(
            key, result, ttl, revision_field="target_revision"
        )
        return result

    async def _compute(
        self,
        query: GetNutritionBulkQuery,
        user_targets: tuple | None = None,
    ) -> dict[str, Any]:
        """Build the bulk nutrition response (uncached)."""
        async with AsyncUnitOfWork() as uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            user_tz = get_zone_info(user_tz_str)
            today = datetime.now(user_tz).date()

            meals = await uow.meals.find_by_date_range(
                query.user_id,
                query.start_date,
                query.end_date,
                user_timezone=user_tz_str,
                projection=MealProjection.MACROS_ONLY,
            )

            if user_targets is None:
                user_targets = await self._get_user_targets(query.user_id)

            (
                target_calories,
                target_macros,
                bmr,
                target_revision,
                macro_preset,
                is_custom,
            ) = user_targets

            meals_by_date: dict[date, list] = {}
            for meal in meals:
                if meal.status == MealStatus.INACTIVE:
                    continue
                meal_date = self._get_meal_date(meal, user_tz_str)
                if meal_date:
                    meals_by_date.setdefault(meal_date, []).append(meal)

            # Fetch hydration entries for range; dedup by legacy_meal_id
            hydration_by_date: dict[date, list] = {}
            try:
                meal_id_set = {m.meal_id for m in meals}
                h_entries = await uow.hydration_entries.find_by_date_range(
                    query.user_id,
                    query.start_date,
                    query.end_date,
                    user_timezone=user_tz_str,
                )
                for entry in h_entries:
                    if entry.legacy_meal_id and entry.legacy_meal_id in meal_id_set:
                        continue
                    entry_date = self._get_hydration_date(entry, user_tz_str)
                    if entry_date:
                        hydration_by_date.setdefault(entry_date, []).append(entry)
            except Exception as exc:
                logger.warning("Failed to fetch bulk hydration data: %s", exc)

            # Single query for the full range; bucket by local date in Python.
            movement_by_date: dict[date, float] = {}
            try:
                overall_start, _ = self._local_day_utc_range(
                    query.start_date, user_tz_str
                )
                _, overall_end = self._local_day_utc_range(query.end_date, user_tz_str)
                included = await uow.movement_entries.fetch_included_kcal_for_range(
                    query.user_id, overall_start, overall_end
                )
                for logged_at, kcal in included:
                    local_date = logged_at.astimezone(user_tz).date()
                    movement_by_date[local_date] = (
                        movement_by_date.get(local_date, 0.0) + kcal
                    )
            except Exception as exc:
                logger.warning("Failed to fetch bulk movement data: %s", exc)

            dates_result: dict[str, dict[str, Any]] = {}
            current = query.start_date
            while current <= query.end_date:
                day_meals = meals_by_date.get(current, [])
                dates_result[current.isoformat()] = self._build_date_summary(
                    day_meals,
                    target_calories,
                    target_macros,
                    movement_kcal=movement_by_date.get(current, 0.0),
                    hydration_entries=hydration_by_date.get(current, []),
                )
                current += timedelta(days=1)

            week_start = get_user_monday(today, query.user_id)
            weekly_budget = await uow.weekly_budgets.find_by_user_and_week(
                query.user_id, week_start
            )

            weekly_summary = None
            if weekly_budget and weekly_budget.target_revision == target_revision:
                try:
                    base_daily = target_calories or (weekly_budget.target_calories / 7)
                    effective = (
                        await WeeklyBudgetService.get_effective_adjusted_daily_async(
                            uow=uow,
                            user_id=query.user_id,
                            week_start=week_start,
                            target_date=today,
                            weekly_budget=weekly_budget,
                            base_daily_cal=base_daily,
                            base_daily_protein=(target_macros or {}).get("protein", 70),
                            base_daily_carbs=(target_macros or {}).get("carbs", 200),
                            base_daily_fat=(target_macros or {}).get("fat", 70),
                            bmr=bmr,
                            user_timezone=user_tz_str,
                        )
                    )
                    adjusted = effective.adjusted
                    adjusted_macros = TdeeCalculationService.apply_adjusted_macro_policy(
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
                    consumed = effective.consumed_total
                    weekly_summary = {
                        "week_start_date": week_start.isoformat(),
                        "target_calories": weekly_budget.target_calories,
                        "consumed_calories": round(consumed["calories"], 1),
                        "remaining_calories": round(
                            weekly_budget.target_calories - consumed["calories"], 1
                        ),
                        "adjusted_daily_calories": adjusted_macros.calories,
                        "adjusted_daily_protein": adjusted_macros.protein,
                        "adjusted_daily_carbs": adjusted_macros.carbs,
                        "adjusted_daily_fat": adjusted_macros.fat,
                        "remaining_days": adjusted.remaining_days,
                        "target_revision": weekly_budget.target_revision,
                    }
                except Exception:
                    logger.exception(
                        "Weekly budget summary failed for user %s; continuing without it",
                        query.user_id,
                    )
            elif weekly_budget:
                logger.warning(
                    "Refusing stale weekly target row for user %s", query.user_id
                )

            cache_version = self._compute_cache_version(dates_result, weekly_summary)

        return {
            "dates": dates_result,
            "weekly_budget": weekly_summary,
            "cache_version": cache_version,
            "profile_target_revision": target_revision,
            "target_revision": target_revision,
            "macro_preset": macro_preset.value,
        }

    def _local_day_utc_range(self, target: date, user_tz_str: str):
        from datetime import datetime, time

        tz = get_zone_info(user_tz_str)
        start_local = datetime.combine(target, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    def _get_meal_date(self, meal, user_tz_str: str) -> date | None:
        """Extract local date from meal's created_at timestamp."""
        if not meal.created_at:
            return None
        tz = get_zone_info(user_tz_str)
        from src.domain.utils.timezone_utils import ensure_utc

        aware_dt = ensure_utc(meal.created_at)
        return aware_dt.astimezone(tz).date()

    def _get_hydration_date(self, entry, user_tz_str: str):
        if not entry.logged_at:
            return None
        tz = get_zone_info(user_tz_str)
        return ensure_utc(entry.logged_at).astimezone(tz).date()

    def _build_date_summary(
        self,
        meals: list,
        target_calories: float | None,
        target_macros: dict | None,
        movement_kcal: float = 0.0,
        hydration_entries: list | None = None,
    ) -> dict[str, Any]:
        """Build summary for a single date."""
        total_protein = 0.0
        total_carbs = 0.0
        total_fat = 0.0
        food_calories = 0.0
        meal_count = 0

        for meal in meals:
            meal_count += 1
            if meal.nutrition and meal.status in [
                MealStatus.READY,
                MealStatus.ENRICHING,
            ]:
                if meal.nutrition.macros:
                    total_protein += meal.nutrition.macros.protein or 0
                    total_carbs += meal.nutrition.macros.carbs or 0
                    total_fat += meal.nutrition.macros.fat or 0
                    food_calories += effective_meal_calories(meal)

        for entry in hydration_entries or []:
            meal_count += 1
            total_protein += entry.protein_g or 0
            total_carbs += entry.carbs_g or 0
            total_fat += entry.fat_g or 0
            food_calories += Macros(
                protein=entry.protein_g or 0,
                carbs=entry.carbs_g or 0,
                fat=entry.fat_g or 0,
                fiber=entry.fiber_g or 0,
            ).total_calories
        net_calories = food_calories - movement_kcal

        target_cal = target_calories or 2000
        target_prot = (target_macros or {}).get("protein", 70)
        target_carb = (target_macros or {}).get("carbs", 200)
        target_fat_val = (target_macros or {}).get("fat", 70)

        return {
            "has_meals": meal_count > 0,
            "meal_count": meal_count,
            "food_calories": round(food_calories, 1),
            "movement_kcal_burned": round(movement_kcal, 1),
            "totals": {
                "consumed": {
                    "calories": round(net_calories, 1),
                    "protein": round(total_protein, 1),
                    "carbs": round(total_carbs, 1),
                    "fat": round(total_fat, 1),
                },
                "target": {
                    "calories": round(target_cal, 1),
                    "protein": round(target_prot, 1),
                    "carbs": round(target_carb, 1),
                    "fat": round(target_fat_val, 1),
                },
                "remaining": {
                    "calories": round(target_cal - net_calories, 1),
                    "protein": round(target_prot - total_protein, 1),
                    "carbs": round(target_carb - total_carbs, 1),
                    "fat": round(target_fat_val - total_fat, 1),
                },
            },
        }

    async def _get_user_targets(self, user_id: str) -> tuple:
        """Get user's TDEE targets. Returns (target_calories, target_macros, bmr)."""
        try:
            from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
                GetUserTdeeQueryHandler,
            )
            from src.app.queries.tdee import GetUserTdeeQuery

            tdee_handler = GetUserTdeeQueryHandler(cache_service=self.cache_service)
            tdee_result = await tdee_handler.handle(GetUserTdeeQuery(user_id=user_id))
            return (
                tdee_result.get("target_calories"),
                tdee_result.get("macros", {}),
                tdee_result.get("bmr", 1800),
                tdee_result.get("profile_target_revision"),
                MacroPreset(tdee_result.get("macro_preset", "standard")),
                bool(tdee_result.get("is_custom")),
            )
        except Exception as e:
            logger.warning(f"Could not fetch TDEE for user {user_id}: {e}")
            return (None, None, 1800, None, MacroPreset.STANDARD, False)

    def _compute_cache_version(self, dates: dict[str, Any], weekly: dict | None) -> str:
        """Compute a version hash for cache staleness detection."""
        import json

        content = json.dumps({"d": dates, "w": weekly}, sort_keys=True)
        return f"v1-{hashlib.md5(content.encode()).hexdigest()[:8]}"

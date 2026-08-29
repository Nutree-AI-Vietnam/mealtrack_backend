"""
GetDailyBreakdownQueryHandler — 7-day macro breakdown (actual vs target).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from src.app.events.base import EventHandler, handles
from src.app.queries.meal.get_daily_breakdown_query import GetDailyBreakdownQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.meal import MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.cache_port import CachePort
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.utils.timezone_utils import (
    get_user_monday,
    get_zone_info,
    resolve_user_timezone_async,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetDailyBreakdownQuery)
class GetDailyBreakdownQueryHandler(
    EventHandler[GetDailyBreakdownQuery, dict[str, Any]]
):
    """Handler for 7-day per-day macro breakdown with consumed vs target."""

    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, query: GetDailyBreakdownQuery) -> dict[str, Any]:
        """Return 7 DailyBreakdownEntry dicts for Mon–Sun of the requested week."""
        async with AsyncUnitOfWork() as uow:
            user_tz_str = await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )
            user_tz = get_zone_info(user_tz_str)
            today = datetime.now(user_tz).date()

            if query.week_start:
                week_start = query.week_start
            else:
                week_start = get_user_monday(today, query.user_id)

            cached = await self._try_get_cached_result(query.user_id, week_start)
            if cached is not None:
                cached_revision = cached.get("profile_target_revision")
                if cached_revision is None:
                    return cached
                profile = await uow.users.get_profile(UUID(query.user_id))
                if profile and cached_revision == profile.profile_target_revision:
                    return cached

            days = [week_start + timedelta(days=i) for i in range(7)]
            meals = await uow.meals.find_by_date_range(
                user_id=query.user_id,
                start_date=week_start,
                end_date=week_start + timedelta(days=6),
                user_timezone=user_tz_str,
                projection=MealProjection.MACROS_ONLY,
            )

        targets = await self._get_base_daily_targets(query.user_id)
        base_daily_cal, base_daily_protein, base_daily_carbs, base_daily_fat = targets[
            :4
        ]
        target_revision = targets[4] if len(targets) > 4 else None

        meals_by_day: dict[date, list[Any]] = {day: [] for day in days}
        for meal in meals:
            meal_day = self._local_meal_date(meal.created_at, user_tz)
            if meal_day in meals_by_day:
                meals_by_day[meal_day].append(meal)

        entries: list[dict[str, Any]] = []
        for day in days:
            total_protein = 0.0
            total_carbs = 0.0
            total_fat = 0.0
            total_calories = 0.0
            meal_count = 0

            for meal in meals_by_day.get(day, []):
                if meal.status == MealStatus.INACTIVE:
                    continue
                meal_count += 1
                if meal.nutrition and meal.status in [
                    MealStatus.READY,
                    MealStatus.ENRICHING,
                ]:
                    if meal.nutrition.macros:
                        total_protein += meal.nutrition.macros.protein or 0.0
                        total_carbs += meal.nutrition.macros.carbs or 0.0
                        total_fat += meal.nutrition.macros.fat or 0.0
                        total_calories += effective_meal_calories(meal)

            entries.append(
                {
                    "date": day.isoformat(),
                    "calories_consumed": round(total_calories, 1),
                    "calories_target": round(base_daily_cal, 1),
                    "protein_consumed": round(total_protein, 1),
                    "protein_target": round(base_daily_protein, 1),
                    "carbs_consumed": round(total_carbs, 1),
                    "carbs_target": round(base_daily_carbs, 1),
                    "fat_consumed": round(total_fat, 1),
                    "fat_target": round(base_daily_fat, 1),
                    "meal_count": meal_count,
                }
            )

        result = {
            "days": entries,
            "week_start": week_start.isoformat(),
        }
        if target_revision is not None:
            result["profile_target_revision"] = target_revision
            result["target_revision"] = target_revision
        await self._write_cache(query.user_id, week_start, result)
        return result

    def _local_meal_date(self, created_at: datetime, user_tz) -> date:
        """Return the user's local date for a meal timestamp."""
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return created_at.astimezone(user_tz).date()

    async def _get_base_daily_targets(
        self, user_id: str
    ) -> tuple[float, float, float, float, int | None]:
        """Return (calories, protein, carbs, fat) base daily targets from TDEE."""
        try:
            from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
                GetUserTdeeQueryHandler,
            )
            from src.app.queries.tdee import GetUserTdeeQuery

            result = await GetUserTdeeQueryHandler(
                cache_service=self.cache_service
            ).handle(GetUserTdeeQuery(user_id=user_id))
            cal = result.get("target_calories", 2000.0)
            macros = result.get("macros", {})
            return (
                cal,
                macros.get("protein", 70.0),
                macros.get("carbs", 200.0),
                macros.get("fat", 70.0),
                result.get("profile_target_revision"),
            )
        except Exception as e:
            logger.warning(f"Could not fetch TDEE targets for {user_id}: {e}")
            return 2000.0, 70.0, 200.0, 70.0, None

    async def _try_get_cached_result(self, user_id: str, week_start: date):
        if not self.cache_service:
            return None
        cache_key, _ = CacheKeys.daily_breakdown(user_id, week_start)
        try:
            return await self.cache_service.get_json(cache_key)
        except Exception as exc:
            logger.warning(
                "Failed to read daily breakdown cache for %s: %s", user_id, exc
            )
            return None

    async def _write_cache(
        self, user_id: str, week_start: date, payload: dict[str, Any]
    ):
        if not self.cache_service:
            return
        cache_key, ttl = CacheKeys.daily_breakdown(user_id, week_start)
        try:
            await self.cache_service.set_json(
                cache_key,
                payload,
                ttl,
                revision_field="profile_target_revision",
            )
        except Exception as exc:
            logger.warning(
                "Failed to write daily breakdown cache for %s: %s", user_id, exc
            )

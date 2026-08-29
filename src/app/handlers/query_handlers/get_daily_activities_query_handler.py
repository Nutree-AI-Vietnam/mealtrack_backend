"""
GetDailyActivitiesQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.activity import GetDailyActivitiesQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.meal import Meal
from src.domain.ports.cache_port import CachePort
from src.domain.services.hydration_catalog_service import (
    localized_name_for_catalog_name,
)
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    resolve_user_timezone_async,
)
from src.domain.services.movement_catalog_service import (
    localized_activity_name,
    localized_activity_name_for_snapshot,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetDailyActivitiesQuery)
class GetDailyActivitiesQueryHandler(
    EventHandler[GetDailyActivitiesQuery, list[dict[str, Any]]]
):
    """Handler for getting daily activities (meals and workouts)."""

    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, query: GetDailyActivitiesQuery) -> list[dict[str, Any]]:
        """Get all activities for the specified date."""
        # Resolve local date from the raw target (tz-aware or tz-naive) using the
        # header timezone before hitting the DB, so the cache key is always local.
        raw = query.target_date
        header_tz = (
            get_zone_info(query.header_timezone) if query.header_timezone else None
        )
        if hasattr(raw, "tzinfo") and raw.tzinfo is not None and header_tz:
            local_date = raw.astimezone(header_tz).date()
        elif hasattr(raw, "date"):
            local_date = raw.date()
        else:
            local_date = raw

        cache_key, ttl = CacheKeys.daily_activities(
            query.user_id, local_date, query.language or "en"
        )
        if self.cache_service:
            cached = await self.cache_service.get_json(cache_key)
            if cached is not None:
                return cached

        meal_activities: list[dict[str, Any]] = []
        workout_activities: list[dict[str, Any]] = []

        fetch_ok = False
        try:
            user_tz_str = await self._resolve_user_timezone(query)
            tz = get_zone_info(user_tz_str)

            # Final local date using the resolved DB timezone (more authoritative
            # than the header, which is used only for the cache-key estimate above).
            if hasattr(raw, "tzinfo") and raw.tzinfo is not None:
                local_date = raw.astimezone(tz).date()
            elif hasattr(raw, "date"):
                local_date = raw.date()

            meal_activities = await self._get_meal_activities(
                query, user_tz_str, local_date
            )
            workout_activities = await self._get_workout_activities(
                query, tz, local_date, query.language or "en"
            )
            fetch_ok = True
        except Exception as e:
            logger.error(f"Error getting activities: {str(e)}", exc_info=True)

        activities = meal_activities + workout_activities
        logger.info(
            f"Retrieved {len(activities)} activities for user {query.user_id} on {local_date}"
        )
        # Only cache a fully-successful fetch. Caching on the error path would
        # pin an empty/partial feed for the whole TTL after a transient DB blip.
        if self.cache_service and fetch_ok:
            await self.cache_service.set_json(cache_key, activities, ttl)
        return activities

    async def _resolve_user_timezone(self, query: GetDailyActivitiesQuery) -> str:
        """Resolve timezone in its own UoW so later reads do not hold its checkout."""
        async with AsyncUnitOfWork() as uow:
            return await resolve_user_timezone_async(
                query.user_id, uow, query.header_timezone
            )

    async def _get_meal_activities(
        self,
        query: GetDailyActivitiesQuery,
        user_tz_str: str,
        local_date,
    ) -> list[dict[str, Any]]:
        """Fetch meals and hydration logs using a short-lived UoW."""
        async with AsyncUnitOfWork() as uow:
            items = await uow.meals.find_by_date(
                local_date,
                user_id=query.user_id,
                user_timezone=user_tz_str,
            )
            hydration_entries = await uow.hydration_entries.find_by_date(
                local_date,
                user_id=query.user_id,
                user_timezone=user_tz_str,
            )
            covered_meal_ids = {
                entry.legacy_meal_id
                for entry in hydration_entries
                if entry.legacy_meal_id
            }

            meal_activities: list[dict[str, Any]] = []
            for item in items:
                if item.meal_type == "hydration":
                    if item.meal_id in covered_meal_ids:
                        continue
                    meal_activities.append(
                        self._build_hydration_activity(item, query.language or "en")
                    )
                    continue
                meal_activities.append(
                    self._build_meal_activity(item, query.target_date, query.language)
                )

            for entry in hydration_entries:
                meal_activities.append(
                    self._build_hydration_entry_activity(entry, query.language or "en")
                )

        return meal_activities

    async def _get_workout_activities(
        self,
        query: GetDailyActivitiesQuery,
        tz,
        local_date,
        language: str,
    ) -> list[dict[str, Any]]:
        """Fetch movement entries using a short-lived UoW."""
        from datetime import time, timedelta

        start_utc = datetime.combine(local_date, time.min, tzinfo=tz).astimezone(UTC)
        end_utc = start_utc + timedelta(days=1)
        async with AsyncUnitOfWork() as uow:
            entries = await uow.movement_entries.find_by_user_and_logged_range(
                query.user_id, start_utc, end_utc
            )
        return [self._build_movement_activity(entry, language) for entry in entries]

    def _build_movement_activity(self, entry, language: str = "en") -> dict[str, Any]:
        title = (
            (
                localized_activity_name(entry.activity_id, language)
                if entry.activity_id and entry.activity_id != "custom"
                else localized_activity_name_for_snapshot(entry.activity_name, language)
            )
            or entry.activity_name
        )
        return {
            "id": entry.id,
            "type": "movement",
            "timestamp": format_iso_utc(entry.logged_at),
            "title": title,
            "activity_id": entry.activity_id,
            "intensity": entry.intensity,
            "duration_min": entry.duration_min,
            "kcal_burned": entry.kcal_burned,
            "source": entry.source,
            "include_in_balance": entry.include_in_balance,
        }

    def _build_meal_activity(
        self, meal, target_date: datetime, language: str = "en"
    ) -> dict[str, Any]:
        meal_type = (
            meal.meal_type
            if hasattr(meal, "meal_type") and meal.meal_type
            else self._determine_meal_type(meal.created_at)
        )
        estimated_weight = self._estimate_meal_weight(meal)
        image_url = meal.image.url if hasattr(meal, "image") and meal.image else None

        title = meal.dish_name or "Unknown Meal"
        if language and language != "en" and meal.translations:
            translation = meal.translations.get(language)
            if translation and translation.dish_name:
                title = translation.dish_name

        return {
            "id": meal.meal_id,
            "type": "meal",
            "timestamp": (
                format_iso_utc(meal.created_at)
                if meal.created_at
                else format_iso_utc(target_date)
            ),
            "title": title,
            "emoji": getattr(meal, "emoji", None),
            "meal_type": meal_type,
            "calories": round(effective_meal_calories(meal), 1),
            "macros": {
                "protein": (
                    round(meal.nutrition.macros.protein, 1) if meal.nutrition else 0
                ),
                "carbs": round(meal.nutrition.macros.carbs, 1) if meal.nutrition else 0,
                "fat": round(meal.nutrition.macros.fat, 1) if meal.nutrition else 0,
            },
            "quantity": estimated_weight,
            "status": meal.status.value if meal.status else "unknown",
            "image_url": image_url,
            "source": getattr(meal, "source", None),
        }

    def _build_hydration_activity(
        self, meal: Meal, language: str = "en"
    ) -> dict[str, Any]:
        kcal = round(meal.nutrition.calories, 1) if meal.nutrition else 0
        macros = {
            "protein": round(meal.nutrition.macros.protein, 1) if meal.nutrition else 0,
            "carbs": round(meal.nutrition.macros.carbs, 1) if meal.nutrition else 0,
            "fat": round(meal.nutrition.macros.fat, 1) if meal.nutrition else 0,
        }
        return {
            "id": meal.meal_id,
            "type": "hydration",
            "timestamp": format_iso_utc(meal.created_at),
            "title": localized_name_for_catalog_name(meal.dish_name, language)
            or "Water",
            "emoji": meal.emoji or "💧",
            "meal_type": "hydration",
            "calories": kcal,
            "macros": macros,
            "quantity": meal.quantity or 0,
            "volume_ml": meal.quantity or 0,
            "status": "completed",
            "image_url": None,
            "source": meal.source or "hydration",
        }

    def _build_hydration_entry_activity(
        self, entry, language: str = "en"
    ) -> dict[str, Any]:
        """Build activity dict from a HydrationEntry domain object (no Meal row)."""
        return {
            "id": entry.id,
            "type": "hydration",
            "timestamp": format_iso_utc(entry.logged_at),
            "title": localized_name_for_catalog_name(
                entry.drink_name_snapshot,
                language,
                drink_id=entry.drink_id,
            )
            or entry.drink_name_snapshot
            or "Water",
            "drink_id": entry.drink_id,
            "emoji": entry.emoji_snapshot or "💧",
            "meal_type": "hydration",
            "calories": round(entry.calories, 1),
            "macros": {
                "protein": round(entry.protein_g or 0, 1),
                "carbs": round(entry.carbs_g or 0, 1),
                "fat": round(entry.fat_g or 0, 1),
            },
            "quantity": entry.credited_ml,
            "volume_ml": entry.volume_ml,
            "status": "completed",
            "image_url": entry.image_url,
            "source": entry.source,
        }

    def _estimate_meal_weight(self, meal) -> float:
        """Estimate meal weight from nutrition data."""
        # Default weight
        estimated_weight = 300.0

        # Check if meal has updated weight
        if hasattr(meal, "weight_grams"):
            return meal.weight_grams

        # Try to get from food items
        if meal.nutrition and meal.nutrition.food_items:
            first_food = meal.nutrition.food_items[0]
            if first_food.unit and "g" in first_food.unit.lower():
                estimated_weight = first_food.quantity
            elif first_food.quantity > 10:  # Assume grams if quantity is large
                estimated_weight = first_food.quantity

        return estimated_weight

    def _determine_meal_type(self, meal_time: datetime) -> str:
        """Determine meal type based on time of day."""
        if not meal_time:
            return "snack"

        hour = meal_time.hour
        if 5 <= hour < 11:
            return "breakfast"
        elif 11 <= hour < 16:
            return "lunch"
        elif hour >= 16 or hour < 1:
            return "dinner"
        else:
            return "snack"

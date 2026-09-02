"""Handler for retrieving distinct recent meals within the last 7 calendar days."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.api.mappers.meal_mapper import MealMapper
from src.app.events.base import EventHandler, handles
from src.app.queries.meal import GetRecentMealsQuery
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.favorite_meal_repository_port import MAX_FAVORITE_MEALS
from src.domain.ports.meal_list_cache_port import MealListCachePort
from src.domain.services.meal_fingerprint_service import (
    compute_meal_content_fingerprint,
    deduplicate_recent_meals,
)
from src.domain.utils.timezone_utils import get_zone_info, utc_now

logger = logging.getLogger(__name__)

# NM-437: recent list covers the last 7 calendar days in the user's timezone
RECENT_WINDOW_DAYS = 7


@handles(GetRecentMealsQuery)
class GetRecentMealsQueryHandler(EventHandler[GetRecentMealsQuery, dict[str, Any]]):
    """Handler for querying recent food meals with revision caching and deduplication."""

    def __init__(
        self,
        uow_factory: Any = None,
        cache_service: MealListCachePort | None = None,
    ):
        self.uow_factory = uow_factory
        self.cache_service = cache_service

    async def handle(self, query: GetRecentMealsQuery) -> dict[str, Any]:
        limit = max(1, min(query.limit, 50))
        language = query.language or "en"
        tz_str = query.user_timezone or "UTC"

        # Check cache if service available
        revision = 0
        if self.cache_service:
            revision = await self.cache_service.get_revision(query.user_id)
            cached = await self.cache_service.get_recent_meals(
                user_id=query.user_id,
                revision=revision,
                timezone_str=tz_str,
                language=language,
            )
            if cached is not None:
                return cached

        # Resolve the last 7 local calendar days (today plus the previous 6)
        tz = get_zone_info(query.user_timezone or "UTC")
        today = utc_now().astimezone(tz).date()

        start_date = today - timedelta(days=RECENT_WINDOW_DAYS - 1)
        start_dt = datetime.combine(
            start_date, datetime.min.time(), tzinfo=tz
        ).astimezone(UTC)
        end_dt = datetime.combine(
            today + timedelta(days=1), datetime.min.time(), tzinfo=tz
        ).astimezone(UTC)

        async with self.uow_factory() as uow:
            raw_meals = await uow.meals.find_recent_food_meals(
                user_id=query.user_id,
                start_dt=start_dt,
                end_dt=end_dt,
                limit=500,
                projection=MealProjection.FULL_WITH_TRANSLATIONS,
            )
            deduped = deduplicate_recent_meals(raw_meals, limit=limit)

            # Favorites are one-per-meal-identity, so flag recent entries by
            # identity fingerprint rather than by meal_id (the favorited row
            # may be an older meal with the same food items and grams).
            favorites = await uow.favorite_meals.list_favorite_meals(
                user_id=query.user_id,
                limit=MAX_FAVORITE_MEALS,
            )
            favorited_fingerprints = {
                compute_meal_content_fingerprint(fav_meal) for fav_meal, _ in favorites
            }

        items = []
        for meal in deduped:
            detailed = MealMapper.to_detailed_response(
                meal,
                target_language=language,
            )
            detailed.is_favorite = (
                compute_meal_content_fingerprint(meal) in favorited_fingerprints
            )
            items.append(detailed.model_dump(mode="json"))

        result = {
            "items": items,
            "total": len(items),
        }

        if self.cache_service:
            await self.cache_service.set_recent_meals(
                user_id=query.user_id,
                revision=revision,
                timezone_str=tz_str,
                language=language,
                data=result,
            )

        return result

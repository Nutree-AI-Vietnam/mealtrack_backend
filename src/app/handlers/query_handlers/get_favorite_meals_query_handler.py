"""Handler for retrieving user's favorite meals ordered newest-favorited first."""

from __future__ import annotations

import logging
from typing import Any

from src.api.mappers.meal_mapper import MealMapper
from src.app.events.base import EventHandler, handles
from src.app.queries.meal import GetFavoriteMealsQuery
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.meal_list_cache_port import MealListCachePort

logger = logging.getLogger(__name__)


@handles(GetFavoriteMealsQuery)
class GetFavoriteMealsQueryHandler(EventHandler[GetFavoriteMealsQuery, dict[str, Any]]):
    """Handler for querying favorite meals with revision caching."""

    def __init__(
        self,
        uow_factory: Any = None,
        cache_service: MealListCachePort | None = None,
    ):
        self.uow_factory = uow_factory
        self.cache_service = cache_service


    async def handle(self, query: GetFavoriteMealsQuery) -> dict[str, Any]:
        limit = max(1, min(query.limit, 100))
        language = query.language or "en"

        # Check cache if service available
        revision = 0
        if self.cache_service:
            revision = await self.cache_service.get_revision(query.user_id)
            cached = await self.cache_service.get_favorite_meals(
                user_id=query.user_id,
                revision=revision,
                language=language,
            )
            if cached is not None:
                return cached

        async with self.uow_factory() as uow:
            favorites = await uow.favorite_meals.list_favorite_meals(
                user_id=query.user_id,
                limit=limit,
            )

        items = []
        for meal, favorited_at in favorites:
            detailed = MealMapper.to_detailed_response(
                meal,
                target_language=language,
            )
            detailed.is_favorite = True
            detailed.favorited_at = favorited_at
            items.append(detailed.model_dump(mode="json"))

        result = {
            "items": items,
            "total": len(items),
        }

        if self.cache_service:
            await self.cache_service.set_favorite_meals(
                user_id=query.user_id,
                revision=revision,
                language=language,
                data=result,
            )

        return result

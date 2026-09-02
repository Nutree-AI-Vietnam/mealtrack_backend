"""Handler for unfavoriting a meal."""

from __future__ import annotations

import logging
from typing import Any

from src.app.commands.meal import UnfavoriteMealCommand
from src.app.events.base import EventHandler, handles
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.favorite_meal_repository_port import MAX_FAVORITE_MEALS
from src.domain.ports.meal_list_cache_port import MealListCachePort
from src.domain.services.meal_fingerprint_service import (
    compute_meal_content_fingerprint,
)

logger = logging.getLogger(__name__)


@handles(UnfavoriteMealCommand)
class UnfavoriteMealCommandHandler(EventHandler[UnfavoriteMealCommand, dict[str, Any]]):
    """Handler for removing a meal from favorites.

    Favorites are one-per-meal-identity, so unfavoriting a meal whose
    identity matches a favorite recorded under a different meal_id (e.g.
    a repeat clone shown in the recent list) removes that favorite too.
    """

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        uow_factory: Any = None,
        cache_service: MealListCachePort | None = None,
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.cache_service = cache_service

    async def handle(self, command: UnfavoriteMealCommand) -> dict[str, Any]:
        async with self.uow_factory() as uow:
            removed = await uow.favorite_meals.unfavorite(
                user_id=command.user_id,
                meal_id=command.meal_id,
            )

            if not removed:
                # No favorite recorded under this meal_id; fall back to
                # removing a favorite with the same meal identity.
                meal = await uow.meals.find_by_id(command.meal_id)
                if meal is not None and meal.user_id == command.user_id:
                    target_fingerprint = compute_meal_content_fingerprint(meal)
                    favorites = await uow.favorite_meals.list_favorite_meals(
                        user_id=command.user_id,
                        limit=MAX_FAVORITE_MEALS,
                    )
                    for fav_meal, _ in favorites:
                        if (
                            compute_meal_content_fingerprint(fav_meal)
                            == target_fingerprint
                        ):
                            await uow.favorite_meals.unfavorite(
                                user_id=command.user_id,
                                meal_id=fav_meal.meal_id,
                            )
                            break

        if self.cache_service:
            await self.cache_service.increment_revision(command.user_id)

        return {
            "meal_id": command.meal_id,
            "is_favorite": False,
            "favorited_at": None,
        }

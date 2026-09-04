"""Handler for favoriting a meal."""

from __future__ import annotations

import logging
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal import FavoriteMealCommand
from src.app.events.base import EventHandler, handles
from src.domain.model.meal import MealStatus
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.meal_list_cache_port import MealListCachePort
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

MAX_FAVORITE_MEALS = 20


@handles(FavoriteMealCommand)
class FavoriteMealCommandHandler(EventHandler[FavoriteMealCommand, dict[str, Any]]):
    """Handler for adding a meal to favorites.

    Favoriting is idempotent. A user may hold at most MAX_FAVORITE_MEALS
    favorites; the next favorite beyond the cap is rejected outright (no
    eviction of older favorites).
    """

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        uow_factory: Any = None,
        cache_service: MealListCachePort | None = None,
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.cache_service = cache_service


    async def handle(self, command: FavoriteMealCommand) -> dict[str, Any]:
        async with self.uow_factory() as uow:
            meal = await uow.meals.find_by_id(command.meal_id)
            if meal is None:
                raise ResourceNotFoundException(
                    f"Meal with ID {command.meal_id} not found"
                )
            if meal.user_id != command.user_id:
                raise AuthorizationException(
                    "You do not have permission to favorite this meal"
                )
            if meal.meal_type == "hydration" or meal.status != MealStatus.READY:
                raise ValidationException("Only ready food meals can be favorited")

            already_favorited = await uow.favorite_meals.is_favorite(
                command.user_id, command.meal_id
            )
            if not already_favorited:
                favorite_count = await uow.favorite_meals.count_favorites(
                    command.user_id
                )
                if favorite_count >= MAX_FAVORITE_MEALS:
                    raise ValidationException(
                        f"Favorites limit of {MAX_FAVORITE_MEALS} reached. "
                        "Remove a favorite before adding a new one.",
                        error_code="FAVORITES_LIMIT_REACHED",
                    )

            now = utc_now()
            await uow.favorite_meals.favorite(
                user_id=command.user_id,
                meal_id=command.meal_id,
                favorited_at=now,
            )

        if self.cache_service:
            await self.cache_service.increment_revision(command.user_id)

        return {
            "meal_id": command.meal_id,
            "is_favorite": True,
            "favorited_at": now,
        }

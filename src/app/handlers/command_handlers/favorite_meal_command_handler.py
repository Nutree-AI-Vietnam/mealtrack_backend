"""Handler for favoriting a meal."""

from __future__ import annotations

import logging
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal import FavoriteMealCommand
from src.app.events.base import EventHandler, handles
from src.domain.model.meal import MealStatus
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.favorite_meal_repository_port import MAX_FAVORITE_MEALS
from src.domain.ports.meal_list_cache_port import MealListCachePort
from src.domain.services.meal_fingerprint_service import (
    compute_meal_content_fingerprint,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(FavoriteMealCommand)
class FavoriteMealCommandHandler(EventHandler[FavoriteMealCommand, dict[str, Any]]):
    """Handler for adding a meal to favorites.

    NM-438 rules:
    - At most one favorite per meal identity (same set of food items and
      grams). Starring the same meal — or a meal with the same identity —
      twice is idempotent.
    - Hard cap of MAX_FAVORITE_MEALS favorites; the next star is rejected
      with a conflict error. The oldest favorite is never evicted.
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

            favorites = await uow.favorite_meals.list_favorite_meals(
                user_id=command.user_id,
                limit=MAX_FAVORITE_MEALS,
            )

            # Idempotent star: same meal, or another meal with the same identity
            target_fingerprint = compute_meal_content_fingerprint(meal)
            for fav_meal, fav_at in favorites:
                if fav_meal.meal_id == command.meal_id or (
                    compute_meal_content_fingerprint(fav_meal) == target_fingerprint
                ):
                    return {
                        "meal_id": command.meal_id,
                        "is_favorite": True,
                        "favorited_at": fav_at,
                    }

            if len(favorites) >= MAX_FAVORITE_MEALS:
                raise ConflictException(
                    f"Favorites limit of {MAX_FAVORITE_MEALS} reached. "
                    "Remove a favorite before adding a new one.",
                    error_code="FAVORITES_LIMIT_REACHED",
                    details={"limit": MAX_FAVORITE_MEALS},
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

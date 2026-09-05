"""Handler for unfavoriting a meal."""

from __future__ import annotations

import logging
from typing import Any

from src.app.commands.meal import UnfavoriteMealCommand
from src.app.events.base import EventHandler, handles
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.meal_list_cache_port import MealListCachePort

logger = logging.getLogger(__name__)


@handles(UnfavoriteMealCommand)
class UnfavoriteMealCommandHandler(EventHandler[UnfavoriteMealCommand, dict[str, Any]]):
    """Handler for removing a meal from favorites."""

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
            await uow.favorite_meals.unfavorite(
                user_id=command.user_id,
                meal_id=command.meal_id,
            )

        if self.cache_service:
            await self.cache_service.increment_revision(command.user_id)

        return {
            "meal_id": command.meal_id,
            "is_favorite": False,
            "favorited_at": None,
        }

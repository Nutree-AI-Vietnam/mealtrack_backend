"""Handler for saving a meal suggestion to user's bookmarks."""

import logging
from typing import Any

from src.app.commands.saved_suggestion import SaveSuggestionCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.cache_port import CachePort

logger = logging.getLogger(__name__)


@handles(SaveSuggestionCommand)
class SaveSuggestionCommandHandler(EventHandler[SaveSuggestionCommand, dict[str, Any]]):
    """Save a meal suggestion. Returns existing if already saved (idempotent)."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        cache_service: CachePort | None = None,
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation or CacheInvalidationService(
            cache_service
        )

    async def handle(self, command: SaveSuggestionCommand) -> dict[str, Any]:
        async with self.uow as uow:
            # Check if already saved (idempotent)
            existing = await uow.saved_suggestions.find_by_user_and_suggestion(
                command.user_id, command.suggestion_id
            )
            if existing:
                return existing

            result = await uow.saved_suggestions.save(
                user_id=command.user_id,
                suggestion_id=command.suggestion_id,
                meal_type=command.meal_type,
                portion_multiplier=command.portion_multiplier,
                suggestion_data=command.suggestion_data,
            )
            logger.info(
                f"Saved suggestion {command.suggestion_id} for user {command.user_id}"
            )
            if self.cache_invalidation and getattr(uow, "outbox", None) is not None:
                await self.cache_invalidation.enqueue_saved_suggestion_invalidation(
                    uow.outbox, command.user_id
                )
            elif self.cache_invalidation:
                await self.cache_invalidation.after_saved_suggestion_write(
                    command.user_id
                )

        return result

"""Handler for deleting a saved suggestion."""

import logging
from typing import Any, Dict, Optional

from src.app.commands.saved_suggestion import DeleteSavedSuggestionCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.ports.cache_port import CachePort
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(DeleteSavedSuggestionCommand)
class DeleteSavedSuggestionCommandHandler(
    EventHandler[DeleteSavedSuggestionCommand, Dict[str, Any]]
):
    """Delete a saved suggestion by suggestion_id for a user."""

    def __init__(
        self,
        cache_service: Optional[CachePort] = None,
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.cache_invalidation = cache_invalidation or CacheInvalidationService(
            cache_service
        )

    async def handle(self, command: DeleteSavedSuggestionCommand) -> Dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            deleted = await uow.saved_suggestions_db.delete_by_user_and_suggestion(
                command.user_id, command.suggestion_id
            )
            if deleted:
                logger.info(
                    "Deleted saved suggestion %s for user %s",
                    command.suggestion_id,
                    command.user_id,
                )
                if self.cache_invalidation and getattr(uow, "outbox", None) is not None:
                    await self.cache_invalidation.enqueue_saved_suggestion_invalidation(
                        uow.outbox, command.user_id
                    )
                elif self.cache_invalidation:
                    await self.cache_invalidation.after_saved_suggestion_write(
                        command.user_id
                    )

        return {"success": deleted}

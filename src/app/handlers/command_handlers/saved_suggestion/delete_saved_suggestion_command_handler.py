"""Handler for deleting a saved suggestion."""

import logging
from typing import Any

from src.app.commands.saved_suggestion import DeleteSavedSuggestionCommand
from src.app.events.base import EventHandler, handles
from src.app.events.saved_suggestion.saved_suggestion_deleted_event import (
    SavedSuggestionDeletedEvent,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(DeleteSavedSuggestionCommand)
class DeleteSavedSuggestionCommandHandler(
    EventHandler[DeleteSavedSuggestionCommand, dict[str, Any]]
):
    """Delete a saved suggestion by suggestion_id for a user."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: DeleteSavedSuggestionCommand) -> dict[str, Any]:
        uow = self.uow or AsyncUnitOfWork()
        async with uow:
            deleted = await uow.saved_suggestions_db.delete_by_user_and_suggestion(
                command.user_id, command.suggestion_id
            )
            if deleted:
                logger.info(
                    "Deleted saved suggestion %s for user %s",
                    command.suggestion_id,
                    command.user_id,
                )

        if deleted:
            event = SavedSuggestionDeletedEvent(
                environment=self.environment,
                aggregate_id=command.suggestion_id,
                data={
                    "user_id": command.user_id,
                },
            )
            await require_event_publisher(self.event_publisher).publish(
                event.to_payload()
            )
            logger.info(
                "Published saved suggestion deleted event event_id=%s user_id=%s suggestion_id=%s",
                event.event_id,
                command.user_id,
                command.suggestion_id,
            )

        return {"success": deleted}

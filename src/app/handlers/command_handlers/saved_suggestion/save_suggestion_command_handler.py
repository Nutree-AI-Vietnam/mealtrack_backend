"""Handler for saving a meal suggestion to user's bookmarks."""

import logging
from typing import Any

from src.app.commands.saved_suggestion import SaveSuggestionCommand
from src.app.events.base import EventHandler, handles
from src.app.events.saved_suggestion.saved_suggestion_created_event import (
    SavedSuggestionCreatedEvent,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(SaveSuggestionCommand)
class SaveSuggestionCommandHandler(EventHandler[SaveSuggestionCommand, dict[str, Any]]):
    """Save a meal suggestion. Returns existing if already saved (idempotent)."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: SaveSuggestionCommand) -> dict[str, Any]:
        uow = self.uow or AsyncUnitOfWork()
        published_needed = False
        async with uow:
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
            published_needed = True
            logger.info(
                f"Saved suggestion {command.suggestion_id} for user {command.user_id}"
            )

        if published_needed and self.event_publisher is not None:
            event = SavedSuggestionCreatedEvent(
                environment=self.environment,
                aggregate_id=command.suggestion_id,
                data={
                    "user_id": command.user_id,
                },
            )
            try:
                await self.event_publisher.publish(event.to_payload())
                logger.info(
                    "Published saved suggestion created event event_id=%s user_id=%s suggestion_id=%s",
                    event.event_id,
                    command.user_id,
                    command.suggestion_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to publish saved suggestion created event event_id=%s error=%s",
                    event.event_id,
                    exc,
                )

        return result


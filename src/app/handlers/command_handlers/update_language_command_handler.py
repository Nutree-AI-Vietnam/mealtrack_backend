"""Handler for updating user language preference."""

import logging
from typing import Any

from src.app.commands.user.update_language_command import (
    SUPPORTED_LANGUAGES,
    UpdateLanguageCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import (
    UserProfileUpdatedEvent,
)
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(UpdateLanguageCommand)
class UpdateLanguageCommandHandler(EventHandler[UpdateLanguageCommand, dict[str, Any]]):
    """Handler for updating user language preference."""

    def __init__(
        self,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        **kwargs: Any,
    ):
        self.event_publisher = event_publisher
        self.environment = environment

    def set_dependencies(self, **kwargs):
        """Set dependencies for dependency injection."""
        if "event_publisher" in kwargs:
            self.event_publisher = kwargs["event_publisher"]

    async def handle(self, command: UpdateLanguageCommand) -> dict[str, Any]:
        """Handle language update command."""
        language = command.language_code.lower().strip()

        if language not in SUPPORTED_LANGUAGES:
            logger.warning(
                f"Invalid language rejected: {language!r} for user {command.user_id}"
            )
            return {"success": False, "error": f"Unsupported language: {language}"}

        async with AsyncUnitOfWork() as uow:
            await uow.users.update_user_language(command.user_id, language)
            await uow.notifications.update_notification_language(
                str(command.user_id), language
            )
            await uow.commit()

        event = UserProfileUpdatedEvent(
            environment=self.environment,
            aggregate_id=str(command.user_id),
            data={
                "user_id": str(command.user_id),
                "language": language,
            },
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())

        logger.info(f"Updated language for user {command.user_id}: {language}")
        return {"success": True, "language_code": language}

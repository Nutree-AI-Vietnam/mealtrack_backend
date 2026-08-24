"""Handler for updating user timezone."""

import logging
from typing import Any

from src.app.commands.user.update_timezone_command import UpdateTimezoneCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import (
    UserProfileUpdatedEvent,
)
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.utils.timezone_utils import is_valid_timezone, normalize_timezone
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(UpdateTimezoneCommand)
class UpdateTimezoneCommandHandler(EventHandler[UpdateTimezoneCommand, dict[str, Any]]):
    """Handler for updating user timezone."""

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

    async def handle(self, command: UpdateTimezoneCommand) -> dict[str, Any]:
        """Handle timezone update command. Skips DB write if timezone is unchanged."""
        logger.info(
            f"Timezone update request: user={command.user_id}, "
            f"timezone={command.timezone!r}"
        )
        if not is_valid_timezone(command.timezone):
            logger.warning(
                f"Invalid timezone rejected: {command.timezone!r} "
                f"for user {command.user_id}"
            )
            return {"success": False, "error": "Invalid timezone"}

        canonical_tz = normalize_timezone(command.timezone)

        # Read: open a UoW just to check the current timezone
        async with AsyncUnitOfWork() as uow:
            current_tz = await uow.users.get_user_timezone(command.user_id)

        if current_tz == canonical_tz:
            logger.debug(
                "Timezone unchanged for user %s: %r - skipping write",
                command.user_id,
                canonical_tz,
            )
            return {"success": True, "timezone": canonical_tz}

        # Write: only open a UoW when we actually need to write
        async with AsyncUnitOfWork() as uow:
            await uow.users.update_user_timezone(command.user_id, canonical_tz)
            await uow.commit()

        logger.info(f"Updated timezone for user {command.user_id}: {canonical_tz}")

        if self.event_publisher is not None:
            try:
                event = UserProfileUpdatedEvent(
                    environment=self.environment,
                    aggregate_id=str(command.user_id),
                    data={
                        "user_id": str(command.user_id),
                        "timezone": canonical_tz,
                    },
                )
                await self.event_publisher.publish(event.to_payload())
            except Exception as exc:
                logger.error("Failed to publish user profile updated event: %s", exc)

        return {"success": True, "timezone": canonical_tz}

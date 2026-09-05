"""Handler for leftover-split daily adjustment preference."""

import logging
from typing import Any

from src.app.commands.user.update_weekly_auto_adjust_command import (
    UpdateWeeklyAutoAdjustCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import UserProfileUpdatedEvent
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(UpdateWeeklyAutoAdjustCommand)
class UpdateWeeklyAutoAdjustCommandHandler(
    EventHandler[UpdateWeeklyAutoAdjustCommand, dict[str, Any]]
):
    """Persist leftover-split preference. Enabled is the default."""

    def __init__(
        self,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        **kwargs: Any,
    ):
        self.event_publisher = event_publisher
        self.environment = environment

    def set_dependencies(self, **kwargs):
        if "event_publisher" in kwargs:
            self.event_publisher = kwargs["event_publisher"]

    async def handle(self, command: UpdateWeeklyAutoAdjustCommand) -> dict[str, Any]:
        enabled = bool(command.enabled)
        user_id = command.user_id

        async with AsyncUnitOfWork() as uow:
            current = await uow.users.get_weekly_auto_adjust(user_id)
            if current == enabled:
                return {"success": True, "enabled": enabled}
            await uow.users.update_user_weekly_auto_adjust(user_id, enabled)
            await uow.commit()

        event = UserProfileUpdatedEvent(
            environment=self.environment,
            aggregate_id=str(user_id),
            data={"user_id": str(user_id), "weekly_auto_adjust": enabled},
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())
        logger.info("Updated weekly auto-adjust for user %s: %s", user_id, enabled)
        return {"success": True, "enabled": enabled}

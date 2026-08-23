"""Command handler for updating an existing movement entry."""

import logging
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.movement import UpdateMovementEntryCommand
from src.app.events.base import EventHandler, handles
from src.app.events.movement.movement_updated_event import MovementUpdatedEvent
from src.app.handlers.command_handlers.log_movement_command_handler import (
    _movement_response,
)
from src.domain.model.movement import MovementIntensity
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.utils.timezone_utils import get_zone_info, resolve_user_timezone_async
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)



@handles(UpdateMovementEntryCommand)
class UpdateMovementEntryCommandHandler(
    EventHandler[UpdateMovementEntryCommand, dict[str, Any]]
):
    def __init__(
        self,
        uow: AsyncUnitOfWork,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, cmd: UpdateMovementEntryCommand) -> dict[str, Any]:
        if cmd.duration_min < 1 or cmd.duration_min > 600:
            raise ValidationException(
                "duration_min must be between 1 and 600", "INVALID_DURATION"
            )
        if cmd.kcal_burned < 0:
            raise ValidationException(
                "kcal_burned must be non-negative", "INVALID_KCAL"
            )
        if cmd.kcal_burned > 5000:
            raise ValidationException(
                "kcal_burned exceeds maximum allowed (5000)", "INVALID_KCAL"
            )
        if cmd.kcal_burned > cmd.duration_min * 30:
            raise ValidationException(
                "kcal_burned is unreasonably high for the given duration",
                "INVALID_KCAL",
            )
        if cmd.intensity not in {item.value for item in MovementIntensity}:
            raise ValidationException("Invalid movement intensity", "INVALID_INTENSITY")

        async with self.uow as uow:
            entry = await uow.movement_entries.find_by_id(cmd.user_id, cmd.entry_id)
            if not entry:
                raise ResourceNotFoundException(
                    "Movement entry not found", "ENTRY_NOT_FOUND"
                )
            if entry.source == "apple_health":
                raise AuthorizationException(
                    "Apple Health entries cannot be edited", "APPLE_HEALTH_NOT_EDITABLE"
                )

            user_tz = await resolve_user_timezone_async(cmd.user_id, uow)
            log_date = entry.logged_at.astimezone(get_zone_info(user_tz)).date()

            updated = await uow.movement_entries.update(
                cmd.user_id,
                cmd.entry_id,
                duration_min=cmd.duration_min,
                kcal_burned=cmd.kcal_burned,
                intensity=cmd.intensity,
                include_in_balance=cmd.include_in_balance,
            )
            integration_event = MovementUpdatedEvent(
                environment=self.environment,
                aggregate_id=cmd.entry_id,
                data={
                    "user_id": cmd.user_id,
                    "log_date": log_date.isoformat(),
                },
            )

        if self.event_publisher is not None:
            try:
                await self.event_publisher.publish(integration_event.to_payload())
                logger.info(
                    "Published movement updated integration event event_id=%s aggregate_id=%s",
                    integration_event.event_id,
                    integration_event.aggregate_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to publish movement updated event event_id=%s error=%s",
                    integration_event.event_id,
                    exc,
                )

        return _movement_response(updated)


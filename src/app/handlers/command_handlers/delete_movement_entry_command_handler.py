import logging
from typing import Any

from src.api.exceptions import AuthorizationException, ResourceNotFoundException
from src.app.commands.movement import DeleteMovementEntryCommand
from src.app.events.base import EventHandler, handles
from src.app.events.movement.movement_deleted_event import MovementDeletedEvent
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.utils.timezone_utils import get_zone_info, resolve_user_timezone_async
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(DeleteMovementEntryCommand)
class DeleteMovementEntryCommandHandler(EventHandler[DeleteMovementEntryCommand, dict]):
    def __init__(
        self,
        uow: AsyncUnitOfWork | None = None,
        uow_factory: Any = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, cmd: DeleteMovementEntryCommand) -> dict:
        async with self.uow_factory() as uow:
            entry = await uow.movement_entries.find_by_id(cmd.user_id, cmd.entry_id)
            if not entry:
                raise ResourceNotFoundException(
                    "Movement entry not found", "ENTRY_NOT_FOUND"
                )
            if entry.source == "apple_health":
                raise AuthorizationException(
                    "Apple Health entries cannot be deleted",
                    "APPLE_HEALTH_NOT_EDITABLE",
                )
            user_tz = await resolve_user_timezone_async(cmd.user_id, uow)
            log_date = entry.logged_at.astimezone(get_zone_info(user_tz)).date()
            deleted = await uow.movement_entries.delete(cmd.user_id, cmd.entry_id)
            if not deleted:
                raise ResourceNotFoundException(
                    "Movement entry not found", "ENTRY_NOT_FOUND"
                )
            integration_event = MovementDeletedEvent(
                environment=self.environment,
                aggregate_id=cmd.entry_id,
                data={
                    "user_id": cmd.user_id,
                    "log_date": log_date.isoformat(),
                },
            )

        await require_event_publisher(self.event_publisher).publish(
            integration_event.to_payload()
        )
        logger.info(
            "Published movement deleted integration event event_id=%s aggregate_id=%s",
            integration_event.event_id,
            integration_event.aggregate_id,
        )

        return {}

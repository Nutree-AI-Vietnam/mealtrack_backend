import logging
from datetime import date
from typing import Any

from src.api.exceptions import AuthorizationException, ResourceNotFoundException
from src.app.commands.movement import DeleteMovementEntryCommand
from src.app.events.base import EventHandler, handles
from src.app.events.movement.local_cache_invalidation import (
    invoke_local_movement_cache_invalidation_hook,
)
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
            removed = await remove_movement_entry(
                uow, cmd.user_id, cmd.entry_id, self.environment
            )
            if removed is None:
                raise ResourceNotFoundException(
                    "Movement entry not found", "ENTRY_NOT_FOUND"
                )
            integration_event, log_date = removed

        await require_event_publisher(self.event_publisher).publish(
            integration_event.to_payload()
        )
        logger.info(
            "Published movement deleted integration event event_id=%s aggregate_id=%s",
            integration_event.event_id,
            integration_event.aggregate_id,
        )
        await invoke_local_movement_cache_invalidation_hook(cmd.user_id, log_date)

        return {}


async def remove_movement_entry(
    uow: Any, user_id: str, entry_id: str, environment: str
) -> tuple[MovementDeletedEvent, date] | None:
    """Delete one of the user's own activity entries in the caller's unit of
    work; None when there is none. Apple Health entries are refused."""
    entry = await uow.movement_entries.find_by_id(user_id, entry_id)
    if not entry:
        return None
    if entry.source == "apple_health":
        raise AuthorizationException(
            "Apple Health entries cannot be deleted",
            "APPLE_HEALTH_NOT_EDITABLE",
        )
    user_tz = await resolve_user_timezone_async(user_id, uow)
    log_date = entry.logged_at.astimezone(get_zone_info(user_tz)).date()
    if not await uow.movement_entries.delete(user_id, entry_id):
        return None
    event = MovementDeletedEvent(
        environment=environment,
        aggregate_id=entry_id,
        data={"user_id": user_id, "log_date": log_date.isoformat()},
    )
    return event, log_date

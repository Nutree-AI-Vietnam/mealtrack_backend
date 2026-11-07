"""Handler for syncing weight entries from mobile."""

import logging
import uuid
from typing import Any, Dict

from src.app.commands.weight import SyncWeightEntriesCommand
from src.app.events.base import EventHandler, handles
from src.domain.model.weight import WeightEntry
from src.domain.utils.timezone_utils import (
    resolve_user_timezone_async,
    wall_time_to_utc,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(SyncWeightEntriesCommand)
class SyncWeightEntriesCommandHandler(
    EventHandler[SyncWeightEntriesCommand, Dict[str, Any]]
):
    """Handler for bulk syncing weight entries."""

    async def handle(self, command: SyncWeightEntriesCommand) -> Dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            timezone = "UTC"
            if any(e.recorded_at.tzinfo is None for e in command.entries):
                timezone = await resolve_user_timezone_async(
                    command.user_id, uow, command.header_timezone
                )
            entries = [
                WeightEntry(
                    id=str(uuid.uuid4()),
                    user_id=command.user_id,
                    weight_kg=e.weight_kg,
                    recorded_at=wall_time_to_utc(e.recorded_at, timezone),
                )
                for e in command.entries
            ]

            synced = await uow.weight_entries.bulk_upsert(command.user_id, entries)
            await uow.commit()

            return {
                "synced_count": synced,
                "message": f"Synced {synced} weight entries",
            }

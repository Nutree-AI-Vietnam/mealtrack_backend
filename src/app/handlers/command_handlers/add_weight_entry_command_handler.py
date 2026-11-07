"""Handler for adding a weight entry."""

import logging
import uuid
from typing import Any, Dict

from src.app.commands.weight import AddWeightEntryCommand
from src.app.events.base import EventHandler, handles
from src.domain.model.weight import WeightEntry
from src.domain.utils.timezone_utils import (
    resolve_user_timezone_async,
    wall_time_to_utc,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(AddWeightEntryCommand)
class AddWeightEntryCommandHandler(EventHandler[AddWeightEntryCommand, Dict[str, Any]]):
    """Handler for adding a weight entry."""

    async def handle(self, command: AddWeightEntryCommand) -> Dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            recorded_at = command.recorded_at
            if recorded_at.tzinfo is None:
                timezone = await resolve_user_timezone_async(
                    command.user_id, uow, command.header_timezone
                )
                recorded_at = wall_time_to_utc(recorded_at, timezone)
            entry = WeightEntry(
                id=str(uuid.uuid4()),
                user_id=command.user_id,
                weight_kg=command.weight_kg,
                recorded_at=recorded_at,
            )

            saved = await uow.weight_entries.add(entry)
            await uow.commit()

            return {
                "id": saved.id,
                "weight_kg": saved.weight_kg,
                "recorded_at": saved.recorded_at.isoformat()
                if saved.recorded_at
                else None,
                "created_at": saved.created_at.isoformat()
                if saved.created_at
                else None,
                "message": "Weight entry added",
            }

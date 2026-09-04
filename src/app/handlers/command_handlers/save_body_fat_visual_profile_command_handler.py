"""Append visual body-fat profile selections without altering measured metrics."""

from src.app.commands.user.save_body_fat_visual_profile_command import (
    SaveBodyFatVisualProfileCommand,
)
from src.app.events.base import EventHandler, handles
from src.domain.model.user.body_fat_visual import BodyFatVisualProfileSelection
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort


@handles(SaveBodyFatVisualProfileCommand)
class SaveBodyFatVisualProfileCommandHandler(
    EventHandler[SaveBodyFatVisualProfileCommand, None]
):
    """Persist each selection as a new record to preserve selection history."""

    def __init__(self, uow: AsyncUnitOfWorkPort):
        self.uow = uow

    async def handle(self, command: SaveBodyFatVisualProfileCommand) -> None:
        async with self.uow as uow:
            await uow.body_fat_visual_profiles.append(
                BodyFatVisualProfileSelection(
                    user_id=command.user_id,
                    schema_version=command.schema_version,
                    range_catalog_version=command.range_catalog_version,
                    sex_at_selection=command.sex_at_selection,
                    start_range_id=command.start_range_id,
                    current_range_id=command.current_range_id,
                    target_range_id=command.target_range_id,
                )
            )

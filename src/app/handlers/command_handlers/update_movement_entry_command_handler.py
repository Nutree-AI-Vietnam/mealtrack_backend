"""Command handler for updating an existing movement entry."""

from typing import Any, Optional

from src.api.exceptions import AuthorizationException, ResourceNotFoundException, ValidationException
from src.app.commands.movement import UpdateMovementEntryCommand
from src.app.events.base import EventHandler, handles
from src.app.handlers.command_handlers.log_movement_command_handler import (
    _movement_response,
)
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.model.movement import MovementIntensity
from src.domain.utils.timezone_utils import get_zone_info, resolve_user_timezone_async
from src.infra.database.uow_async import AsyncUnitOfWork


@handles(UpdateMovementEntryCommand)
class UpdateMovementEntryCommandHandler(
    EventHandler[UpdateMovementEntryCommand, dict[str, Any]]
):
    def __init__(
        self,
        uow: AsyncUnitOfWork,
        cache_invalidation: Optional[CacheInvalidationService] = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation

    async def handle(self, cmd: UpdateMovementEntryCommand) -> dict[str, Any]:
        if cmd.duration_min < 1 or cmd.duration_min > 600:
            raise ValidationException("duration_min must be between 1 and 600", "INVALID_DURATION")
        if cmd.kcal_burned < 0:
            raise ValidationException("kcal_burned must be non-negative", "INVALID_KCAL")
        if cmd.kcal_burned > 5000:
            raise ValidationException("kcal_burned exceeds maximum allowed (5000)", "INVALID_KCAL")
        if cmd.kcal_burned > cmd.duration_min * 30:
            raise ValidationException("kcal_burned is unreasonably high for the given duration", "INVALID_KCAL")
        if cmd.intensity not in {item.value for item in MovementIntensity}:
            raise ValidationException("Invalid movement intensity", "INVALID_INTENSITY")

        async with self.uow as uow:
            entry = await uow.movement_entries.find_by_id(cmd.user_id, cmd.entry_id)
            if not entry:
                raise ResourceNotFoundException("Movement entry not found", "ENTRY_NOT_FOUND")
            if entry.source == "apple_health":
                raise AuthorizationException("Apple Health entries cannot be edited", "APPLE_HEALTH_NOT_EDITABLE")

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

        if self.cache_invalidation:
            await self.cache_invalidation.schedule_after_movement_write(
                cmd.user_id, log_date
            )

        return _movement_response(updated)

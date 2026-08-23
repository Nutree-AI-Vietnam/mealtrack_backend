"""Command handler for logging a movement entry."""

import logging
from datetime import date, timedelta
from typing import Optional

from src.api.exceptions import ValidationException
from src.app.commands.movement import LogMovementCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.model.movement import MovementEntry, MovementIntensity
from src.domain.services.movement_catalog_service import get_activity, get_met
from src.domain.utils.timezone_utils import (
    format_iso_utc,
    get_zone_info,
    noon_utc_for_date,
    resolve_user_timezone_async,
    utc_now,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


def _movement_response(entry: MovementEntry) -> dict:
    return {
        "id": entry.id,
        "activity_id": entry.activity_id,
        "activity_name": entry.activity_name,
        "duration_min": entry.duration_min,
        "kcal_burned": entry.kcal_burned,
        "intensity": entry.intensity,
        "source": entry.source,
        "include_in_balance": entry.include_in_balance,
        "logged_at": format_iso_utc(entry.logged_at),
    }


def _validate_log_movement(cmd: LogMovementCommand) -> None:
    if not cmd.activity_name.strip() or len(cmd.activity_name) > 100:
        raise ValidationException("Invalid movement activity", "INVALID_ACTIVITY")
    if cmd.duration_min < 1 or cmd.duration_min > 600:
        raise ValidationException(
            "Duration must be between 1 and 600 minutes", "INVALID_DURATION"
        )
    if cmd.kcal_burned < 0:
        raise ValidationException(
            "Calories burned cannot be negative", "INVALID_KCAL"
        )
    if cmd.kcal_burned > 5000:
        raise ValidationException(
            "kcal_burned exceeds maximum allowed (5000)", "INVALID_KCAL"
        )
    if cmd.kcal_burned > cmd.duration_min * 30:
        raise ValidationException(
            "kcal_burned is unreasonably high for the given duration", "INVALID_KCAL"
        )
    valid_intensities = {item.value for item in MovementIntensity}
    if cmd.intensity not in valid_intensities:
        raise ValidationException("Invalid movement intensity", "INVALID_INTENSITY")
    if cmd.activity_id is not None:
        if get_activity(cmd.activity_id) is None:
            raise ValidationException("Unknown movement activity", "INVALID_ACTIVITY")
        if get_met(cmd.activity_id, cmd.intensity) is None:
            raise ValidationException(
                "Activity does not support movement intensity", "INVALID_INTENSITY"
            )


@handles(LogMovementCommand)
class LogMovementCommandHandler(EventHandler[LogMovementCommand, dict]):
    def __init__(
        self,
        uow: AsyncUnitOfWork,
        cache_invalidation: Optional[CacheInvalidationService] = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation

    async def handle(self, cmd: LogMovementCommand) -> dict:
        _validate_log_movement(cmd)

        async with self.uow as uow:
            user_tz = await resolve_user_timezone_async(
                cmd.user_id, uow, cmd.header_timezone
            )
            now_utc = utc_now()
            today = now_utc.astimezone(get_zone_info(user_tz)).date()
            log_date = cmd.target_date or today
            if log_date > today + timedelta(days=1):
                raise ValidationException(
                    "Movement date cannot be more than one day in the future",
                    "INVALID_DATE",
                )
            logged_at = (
                noon_utc_for_date(log_date, user_tz) if cmd.target_date else now_utc
            )

            entry = MovementEntry(
                user_id=cmd.user_id,
                activity_id=cmd.activity_id,
                activity_name=cmd.activity_name,
                duration_min=cmd.duration_min,
                kcal_burned=cmd.kcal_burned,
                intensity=cmd.intensity,
                include_in_balance=cmd.include_in_balance,
                logged_at=logged_at,
            )
            saved = await uow.movement_entries.add(entry)

        if self.cache_invalidation:
            await self.cache_invalidation.schedule_after_movement_write(
                cmd.user_id, log_date
            )

        return _movement_response(saved)

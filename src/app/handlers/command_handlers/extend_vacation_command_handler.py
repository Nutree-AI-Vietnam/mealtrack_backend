"""Move the last day of an open vacation later."""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.vacation.extend_vacation_command import ExtendVacationCommand
from src.app.events.base import EventHandler, handles
from src.app.services.vacation_support import (
    invalidate_vacation_views,
    vacation_payload,
)
from src.domain.ports.cache_port import CachePort
from src.domain.services.vacation_rules import open_vacation
from src.domain.utils.timezone_utils import resolve_user_timezone_async, user_today
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(ExtendVacationCommand)
class ExtendVacationCommandHandler(EventHandler[ExtendVacationCommand, dict[str, Any]]):
    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, command: ExtendVacationCommand) -> dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            user_tz = await resolve_user_timezone_async(command.user_id, uow)
            today = user_today(user_tz)
            current = open_vacation(await uow.vacations.find_by_user(command.user_id), today)
            if current is None:
                raise ResourceNotFoundException(
                    message="There is no vacation to extend.",
                    error_code="VACATION_NOT_FOUND",
                )
            if command.end_date <= current.effective_end:
                raise ValidationException(
                    message="To come back sooner, end vacation.",
                    error_code="VACATION_EXTEND_NOT_LATER",
                )
            current.end_date = command.end_date
            current.ended_on = None
            await uow.vacations.save(current)
            await uow.commit()

        await invalidate_vacation_views(self.cache_service, command.user_id)
        logger.info(
            "Extended vacation user_id=%s end=%s",
            command.user_id,
            command.end_date.isoformat(),
        )
        return {"vacation": vacation_payload(current, today)}

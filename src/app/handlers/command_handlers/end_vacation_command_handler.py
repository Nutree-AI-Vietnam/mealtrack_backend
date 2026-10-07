"""Cancel an open vacation so today is a normal day."""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.vacation.end_vacation_command import EndVacationCommand
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


@handles(EndVacationCommand)
class EndVacationCommandHandler(EventHandler[EndVacationCommand, dict[str, Any]]):
    def __init__(self, cache_service: CachePort | None = None):
        self.cache_service = cache_service

    async def handle(self, command: EndVacationCommand) -> dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            user_tz = await resolve_user_timezone_async(command.user_id, uow)
            today = user_today(user_tz)
            current = open_vacation(await uow.vacations.find_by_user(command.user_id), today)
            if current is None:
                raise ResourceNotFoundException(
                    message="There is no vacation to end.",
                    error_code="VACATION_NOT_FOUND",
                )
            if current.drop_today(today):
                await uow.vacations.delete(current.vacation_id)
                await uow.commit()
                payload = None
            else:
                await uow.vacations.save(current)
                await uow.commit()
                payload = vacation_payload(current, today)

        await invalidate_vacation_views(self.cache_service, command.user_id)
        logger.info("Ended vacation user_id=%s", command.user_id)
        return {"vacation": payload}

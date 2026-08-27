"""Handler for unmarking a cheat day."""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.cheat_day import UnmarkCheatDayCommand
from src.app.events.base import EventHandler, handles
from src.app.events.cheat_day.cheat_day_unmarked_event import (
    CheatDayUnmarkedEvent,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(UnmarkCheatDayCommand)
class UnmarkCheatDayCommandHandler(EventHandler[UnmarkCheatDayCommand, dict[str, Any]]):
    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: UnmarkCheatDayCommand) -> dict[str, Any]:
        uow = self.uow or AsyncUnitOfWork()
        async with uow:
            try:
                existing = await uow.cheat_days.find_by_user_and_date(
                    command.user_id, command.date
                )
                if not existing:
                    raise ResourceNotFoundException(
                        message=f"No cheat day found for date {command.date}",
                        error_code="CHEAT_DAY_NOT_FOUND",
                    )

                cheat_day_id = existing.cheat_day_id
                await uow.cheat_days.delete(cheat_day_id)
                await uow.commit()

                result = {
                    "date": command.date.isoformat(),
                    "message": "Cheat day unmarked",
                }
            except ResourceNotFoundException:
                await uow.rollback()
                raise
            except Exception:
                await uow.rollback()
                raise

        event = CheatDayUnmarkedEvent(
            environment=self.environment,
            aggregate_id=cheat_day_id,
            data={
                "user_id": command.user_id,
                "date": command.date.isoformat(),
            },
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())
        logger.info(
            "Published cheat day unmarked event event_id=%s user_id=%s date=%s",
            event.event_id,
            command.user_id,
            command.date.isoformat(),
        )

        return result

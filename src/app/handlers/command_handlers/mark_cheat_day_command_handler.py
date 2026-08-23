"""Handler for marking a cheat day."""

import logging
import uuid
from typing import Any

from src.api.exceptions import ValidationException
from src.app.commands.cheat_day import MarkCheatDayCommand
from src.app.events.base import EventHandler, handles
from src.app.events.cheat_day.cheat_day_marked_event import CheatDayMarkedEvent
from src.domain.model.cheat_day import CheatDay
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.utils.timezone_utils import (
    resolve_user_timezone_async,
    user_today,
    utc_now,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(MarkCheatDayCommand)
class MarkCheatDayCommandHandler(EventHandler[MarkCheatDayCommand, dict[str, Any]]):
    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: MarkCheatDayCommand) -> dict[str, Any]:
        uow = self.uow or AsyncUnitOfWork()
        async with uow:
            try:
                target_date = command.date
                user_tz = await resolve_user_timezone_async(command.user_id, uow)
                today = user_today(user_tz)

                if target_date < today:
                    raise ValidationException(
                        message="Cannot mark past dates as cheat days",
                        error_code="PAST_DATE_NOT_ALLOWED",
                    )

                existing = await uow.cheat_days.find_by_user_and_date(
                    command.user_id, target_date
                )
                if existing:
                    raise ValidationException(
                        message=f"Date {target_date} is already marked as cheat day",
                        error_code="ALREADY_MARKED",
                    )

                cheat_day = CheatDay(
                    cheat_day_id=str(uuid.uuid4()),
                    user_id=command.user_id,
                    date=target_date,
                    marked_at=utc_now(),
                )

                await uow.cheat_days.add(cheat_day)
                await uow.commit()

                result = {
                    "cheat_day_id": cheat_day.cheat_day_id,
                    "date": target_date.isoformat(),
                    "message": "Date marked as cheat day",
                }
            except ValidationException:
                await uow.rollback()
                raise
            except Exception:
                await uow.rollback()
                raise

        if self.event_publisher is not None:
            event = CheatDayMarkedEvent(
                environment=self.environment,
                aggregate_id=cheat_day.cheat_day_id,
                data={
                    "user_id": command.user_id,
                    "date": target_date.isoformat(),
                },
            )
            try:
                await self.event_publisher.publish(event.to_payload())
                logger.info(
                    "Published cheat day marked event event_id=%s user_id=%s date=%s",
                    event.event_id,
                    command.user_id,
                    target_date.isoformat(),
                )
            except Exception as exc:
                logger.error(
                    "Failed to publish cheat day marked event event_id=%s error=%s",
                    event.event_id,
                    exc,
                )

        return result


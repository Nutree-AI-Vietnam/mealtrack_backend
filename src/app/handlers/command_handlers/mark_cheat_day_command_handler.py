"""Handler for marking a cheat day."""

import logging
import uuid
from typing import Any

from src.api.exceptions import ValidationException
from src.app.commands.cheat_day import MarkCheatDayCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.model.cheat_day import CheatDay
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
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
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation

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
                if self.cache_invalidation and getattr(uow, "outbox", None) is not None:
                    import inspect

                    res = self.cache_invalidation.enqueue_cheat_day_invalidation(
                        uow.outbox, command.user_id, target_date
                    )
                    if inspect.isawaitable(res):
                        await res
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

        if self.cache_invalidation and getattr(uow, "outbox", None) is None:
            import inspect

            res = self.cache_invalidation.after_cheat_day_write(
                command.user_id, target_date
            )
            if inspect.isawaitable(res):
                await res

        return result

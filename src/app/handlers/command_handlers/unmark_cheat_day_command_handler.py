"""Handler for unmarking a cheat day."""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.cheat_day import UnmarkCheatDayCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(UnmarkCheatDayCommand)
class UnmarkCheatDayCommandHandler(EventHandler[UnmarkCheatDayCommand, dict[str, Any]]):
    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation

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

                await uow.cheat_days.delete(existing.cheat_day_id)
                if self.cache_invalidation and getattr(uow, "outbox", None) is not None:
                    import inspect

                    res = self.cache_invalidation.enqueue_cheat_day_invalidation(
                        uow.outbox, command.user_id, command.date
                    )
                    if inspect.isawaitable(res):
                        await res
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

        if self.cache_invalidation and getattr(uow, "outbox", None) is None:
            import inspect

            res = self.cache_invalidation.after_cheat_day_write(
                command.user_id, command.date
            )
            if inspect.isawaitable(res):
                await res

        return result

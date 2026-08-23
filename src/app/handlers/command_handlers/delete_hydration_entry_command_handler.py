"""Command handler for soft-deleting a hydration log entry."""

import logging

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.hydration.delete_hydration_entry_command import (
    DeleteHydrationEntryCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.model.meal import MealStatus
from src.domain.utils.timezone_utils import (
    get_zone_info,
    resolve_user_timezone_async,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(DeleteHydrationEntryCommand)
class DeleteHydrationEntryCommandHandler(
    EventHandler[DeleteHydrationEntryCommand, dict]
):
    def __init__(
        self,
        uow: AsyncUnitOfWork,
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation

    async def handle(self, cmd: DeleteHydrationEntryCommand) -> dict:
        async with self.uow as uow:
            entry = await uow.hydration_entries.find_by_id_or_legacy_meal_id(
                cmd.user_id,
                cmd.entry_id,
            )
            if entry is not None:
                await uow.hydration_entries.delete_by_id_or_legacy_meal_id(
                    cmd.user_id,
                    cmd.entry_id,
                )
                meal = (
                    await uow.meals.find_by_id(entry.legacy_meal_id)
                    if entry.legacy_meal_id
                    else None
                )
                if meal is not None and meal.status != MealStatus.INACTIVE:
                    await uow.meals.save(meal.mark_inactive())

                user_tz = await resolve_user_timezone_async(cmd.user_id, uow)
                tz = get_zone_info(user_tz)
                log_date = entry.logged_at.astimezone(tz).date()
            else:
                meal = await uow.meals.find_by_id(cmd.entry_id)
                if meal is None or meal.user_id != cmd.user_id:
                    raise ResourceNotFoundException("Hydration entry not found")
                if meal.meal_type != "hydration":
                    raise ResourceNotFoundException("Hydration entry not found")

                if meal.status != MealStatus.INACTIVE:
                    await uow.meals.save(meal.mark_inactive())

                user_tz = await resolve_user_timezone_async(cmd.user_id, uow)
                tz = get_zone_info(user_tz)
                log_date = meal.created_at.astimezone(tz).date()

            if self.cache_invalidation and getattr(uow, "outbox", None) is not None:
                await self.cache_invalidation.enqueue_hydration_invalidation(
                    uow.outbox, cmd.user_id, log_date
                )
            elif self.cache_invalidation:
                await self.cache_invalidation.after_hydration_write(
                    cmd.user_id, log_date
                )

        return {"success": True}

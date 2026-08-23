"""
Handler for hard-deleting meals with preservation of food item data.

This handler delegates the actual deletion logic to the meal repository,
which performs soft-deletes on translations/food items and hard-deletes
on nutrition/meal records.
"""

import logging
from typing import Any

from src.api.exceptions import AuthorizationException
from src.app.commands.meal import DeleteMealCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(DeleteMealCommand)
class DeleteMealCommandHandler(EventHandler[DeleteMealCommand, dict[str, Any]]):
    """Handler for hard-deleting a meal with data preservation."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation

    async def handle(self, command: DeleteMealCommand) -> dict[str, Any]:
        """Handle meal deletion with data preservation."""
        deleted_kind = "meal"
        hydration_cache_event_enqueued = False
        async with self.uow as uow:
            meal = await uow.meals.find_by_id(command.meal_id)
            if meal is not None:
                if meal.user_id != command.user_id:
                    raise AuthorizationException(
                        "You do not have permission to delete this meal"
                    )
                # Recommended-meal logs set meal_recommendations.logged_meal_id
                # with ON DELETE SET NULL. Clearing only the FK leaves logged_at
                # set and violates ck_meal_recommendations_logged_coherent.
                plans = getattr(uow, "meal_recommendation_plans", None)
                if plans is not None:
                    await plans.clear_links_for_deleted_meal(meal_id=command.meal_id)
                await uow.meals.delete(command.meal_id)
                log_date = (meal.created_at or utc_now()).date()
                if self.cache_invalidation:
                    await self.cache_invalidation.enqueue_meal_invalidation(
                        uow.outbox,
                        command.user_id,
                        log_date,
                    )
            else:
                hydration_entries = getattr(uow, "hydration_entries", None)
                hydration_entry = (
                    await hydration_entries.find_by_id_or_legacy_meal_id(
                        command.user_id,
                        command.meal_id,
                    )
                    if hydration_entries is not None
                    else None
                )
                if hydration_entry is not None:
                    await hydration_entries.delete_by_id_or_legacy_meal_id(
                        command.user_id,
                        command.meal_id,
                    )
                    deleted_kind = "hydration"
                    log_date = hydration_entry.logged_at.date()
                    if (
                        self.cache_invalidation
                        and getattr(uow, "outbox", None) is not None
                    ):
                        event_id = await self.cache_invalidation.enqueue_hydration_invalidation(
                            uow.outbox,
                            command.user_id,
                            log_date,
                        )
                        hydration_cache_event_enqueued = event_id is not None
                else:
                    return {
                        "meal_id": command.meal_id,
                        "message": "Meal already deleted",
                    }

        if (
            self.cache_invalidation
            and deleted_kind == "hydration"
            and not hydration_cache_event_enqueued
        ):
            await self.cache_invalidation.after_hydration_write(
                command.user_id, log_date
            )

        return {
            "meal_id": command.meal_id,
            "message": (
                "Hydration entry deleted"
                if deleted_kind == "hydration"
                else "Meal deleted, ingredient data preserved"
            ),
        }

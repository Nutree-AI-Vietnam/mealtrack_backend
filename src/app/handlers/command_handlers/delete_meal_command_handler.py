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
from src.app.events.hydration.hydration_caloric_deleted_event import (
    HydrationCaloricDeletedEvent,
)
from src.app.events.hydration.hydration_deleted_event import (
    HydrationDeletedEvent,
)
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.model.hydration import DrinkCategory
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.services.hydration_catalog_service import find_by_id
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(DeleteMealCommand)
class DeleteMealCommandHandler(EventHandler[DeleteMealCommand, dict[str, Any]]):
    """Handler for hard-deleting a meal with data preservation."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        cache_invalidation: CacheInvalidationService | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.cache_invalidation = cache_invalidation
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: DeleteMealCommand) -> dict[str, Any]:
        """Handle meal deletion with data preservation."""
        deleted_kind = "meal"
        hydration_delete_event = None
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

                    drink_id = getattr(hydration_entry, "drink_id", None)
                    drink = find_by_id(drink_id) if drink_id else None
                    carbs_g = float(getattr(hydration_entry, "carbs_g", 0.0) or 0.0)
                    fat_g = float(getattr(hydration_entry, "fat_g", 0.0) or 0.0)
                    protein_g = float(getattr(hydration_entry, "protein_g", 0.0) or 0.0)
                    is_caloric = (
                        drink is not None and drink.category == DrinkCategory.CALORIC
                    ) or (carbs_g > 0 or fat_g > 0 or protein_g > 0)
                    if is_caloric:
                        hydration_delete_event = HydrationCaloricDeletedEvent(
                            environment=self.environment,
                            aggregate_id=hydration_entry.id,
                            data={
                                "user_id": command.user_id,
                                "log_date": log_date.isoformat(),
                            },
                        )
                    else:
                        hydration_delete_event = HydrationDeletedEvent(
                            environment=self.environment,
                            aggregate_id=hydration_entry.id,
                            data={
                                "user_id": command.user_id,
                                "log_date": log_date.isoformat(),
                            },
                        )
                else:
                    return {
                        "meal_id": command.meal_id,
                        "message": "Meal already deleted",
                    }

        if deleted_kind == "hydration" and self.event_publisher is not None and hydration_delete_event is not None:
            try:
                await self.event_publisher.publish(
                    hydration_delete_event.to_payload()
                )
                logger.info(
                    "Published hydration deleted integration event event_id=%s aggregate_id=%s",
                    hydration_delete_event.event_id,
                    hydration_delete_event.aggregate_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to publish hydration deleted event event_id=%s error=%s",
                    hydration_delete_event.event_id,
                    exc,
                )

        return {
            "meal_id": command.meal_id,
            "message": (
                "Hydration entry deleted"
                if deleted_kind == "hydration"
                else "Meal deleted, ingredient data preserved"
            ),
        }


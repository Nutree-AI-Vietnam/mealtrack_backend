"""Command handler for soft-deleting a hydration log entry."""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.hydration.delete_hydration_entry_command import (
    DeleteHydrationEntryCommand,
)
from src.app.events.base import EventHandler, handles
from src.app.events.hydration.hydration_caloric_deleted_event import (
    HydrationCaloricDeletedEvent,
)
from src.app.events.hydration.hydration_deleted_event import (
    HydrationDeletedEvent,
)
from src.domain.model.hydration import DrinkCategory
from src.domain.model.meal import MealStatus
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.services.hydration_catalog_service import find_by_id
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
        uow: AsyncUnitOfWork | None = None,
        uow_factory: Any = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, cmd: DeleteHydrationEntryCommand) -> dict:
        async with self.uow_factory() as uow:
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

                drink = find_by_id(entry.drink_id)
                is_caloric = (
                    drink is not None and drink.category == DrinkCategory.CALORIC
                ) or (entry.carbs_g > 0 or entry.fat_g > 0 or entry.protein_g > 0)
                if is_caloric:
                    integration_event = HydrationCaloricDeletedEvent(
                        environment=self.environment,
                        aggregate_id=entry.id,
                        data={
                            "user_id": cmd.user_id,
                            "log_date": log_date.isoformat(),
                        },
                    )
                else:
                    integration_event = HydrationDeletedEvent(
                        environment=self.environment,
                        aggregate_id=entry.id,
                        data={
                            "user_id": cmd.user_id,
                            "log_date": log_date.isoformat(),
                        },
                    )
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
                integration_event = HydrationDeletedEvent(
                    environment=self.environment,
                    aggregate_id=meal.meal_id,
                    data={
                        "user_id": cmd.user_id,
                        "log_date": log_date.isoformat(),
                    },
                )

        await require_event_publisher(self.event_publisher).publish(
            integration_event.to_payload()
        )
        logger.info(
            "Published hydration deleted integration event event_id=%s aggregate_id=%s",
            integration_event.event_id,
            integration_event.aggregate_id,
        )

        return {"success": True}

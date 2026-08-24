"""
Handler for adding custom ingredients to meals.
"""

import logging
from typing import Any

from src.app.commands.meal import AddCustomIngredientCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import MealUpdatedEvent
from src.domain.model.meal.food_item_change import FoodItemChange
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.services.meal_service import MealService
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(AddCustomIngredientCommand)
class AddCustomIngredientCommandHandler(
    EventHandler[AddCustomIngredientCommand, dict[str, Any]]
):
    """Handler for adding custom ingredients to meals."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: AddCustomIngredientCommand) -> dict[str, Any]:
        """Handle adding custom ingredient to meal."""
        try:
            async with self.uow as uow:
                meal = await uow.meals.find_by_id(command.meal_id)
                if not meal:
                    raise ValueError(f"Meal {command.meal_id} not found")

                change = FoodItemChange(
                    action="add",
                    name=command.name,
                    quantity=command.quantity,
                    unit=command.unit,
                    custom_nutrition=command.nutrition,
                )

                meal_service = MealService()
                updated_meal = meal_service.apply_food_item_changes(meal, [change])
                saved_meal = await uow.meals.save(updated_meal)
                meal_date = (saved_meal.created_at or utc_now()).date()

            if self.event_publisher is not None:
                try:
                    event = MealUpdatedEvent(
                        environment=self.environment,
                        aggregate_id=saved_meal.meal_id,
                        data={
                            "user_id": saved_meal.user_id,
                            "meal_id": saved_meal.meal_id,
                            "meal_date": meal_date.isoformat(),
                        },
                    )
                    await self.event_publisher.publish(event.to_payload())
                except Exception as exc:
                    logger.error("Failed to publish meal updated event: %s", exc)

            return {
                "success": True,
                "meal_id": saved_meal.meal_id,
                "message": f"Added custom ingredient: {command.name}",
            }
        except Exception:
            raise

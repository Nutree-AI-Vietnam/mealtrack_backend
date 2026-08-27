"""Handler for detaching uploaded meal photos."""

import logging
from typing import Any

from src.api.exceptions import AuthorizationException, ResourceNotFoundException
from src.app.commands.meal import DeleteMealPhotoCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import MealUpdatedEvent
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(DeleteMealPhotoCommand)
class DeleteMealPhotoCommandHandler(
    EventHandler[DeleteMealPhotoCommand, dict[str, Any]]
):
    """Detach a meal photo from a meal owned by the user."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: DeleteMealPhotoCommand) -> dict[str, Any]:
        async with self.uow as uow:
            try:
                meal = await uow.meals.find_by_id(
                    command.meal_id, projection=MealProjection.FULL
                )
                if not meal:
                    raise ResourceNotFoundException("Meal not found")
                if meal.user_id != command.user_id:
                    raise AuthorizationException(
                        "You do not have permission to modify this meal"
                    )

                saved_meal = await uow.meals.save(meal.without_image())
                meal_date = (saved_meal.created_at or utc_now()).date()
                await uow.commit()

                response = {
                    "success": True,
                    "meal_id": saved_meal.meal_id,
                    "image_url": None,
                }
            except Exception:
                await uow.rollback()
                raise

        event = MealUpdatedEvent(
            environment=self.environment,
            aggregate_id=saved_meal.meal_id,
            data={
                "user_id": saved_meal.user_id,
                "meal_id": saved_meal.meal_id,
                "meal_date": meal_date.isoformat(),
            },
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())

        return response

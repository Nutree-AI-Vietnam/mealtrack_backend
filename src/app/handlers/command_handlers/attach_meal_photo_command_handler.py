"""Handler for attaching uploaded meal photos."""

import logging
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal import AttachMealPhotoCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import MealUpdatedEvent
from src.domain.model.meal import MealImage, MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(AttachMealPhotoCommand)
class AttachMealPhotoCommandHandler(
    EventHandler[AttachMealPhotoCommand, dict[str, Any]]
):
    """Attach a validated uploaded photo to a meal owned by the user."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        uow_factory: Any = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: AttachMealPhotoCommand) -> dict[str, Any]:
        async with self.uow_factory() as uow:
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
                if meal.status != MealStatus.READY:
                    raise ValidationException(
                        "Meal must be in READY status to attach a photo"
                    )

                image = MealImage(
                    image_id=command.image_id,
                    format=command.image_format,
                    size_bytes=command.size_bytes,
                    url=command.image_url,
                )
                updated_meal = meal.with_image(image)
                saved_meal = await uow.meals.save(updated_meal)
                meal_date = (saved_meal.created_at or utc_now()).date()
                await uow.commit()

                response = {
                    "success": True,
                    "meal_id": saved_meal.meal_id,
                    "image_url": saved_meal.image.url if saved_meal.image else None,
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

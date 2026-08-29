"""Handler for repeating a meal idempotently."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal import RepeatMealCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import publish_meal_event
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.model.nutrition import FoodItem, Macros, Nutrition
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.meal_list_cache_port import MealListCachePort
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


@handles(RepeatMealCommand)
class RepeatMealCommandHandler(EventHandler[RepeatMealCommand, Meal]):
    """Handler for cloning and logging a past active or favorited meal."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        uow_factory: Any = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        event_bus: Any | None = None,
        cache_service: MealListCachePort | None = None,
        environment: str = "development",
    ):

        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.event_publisher = event_publisher
        self.event_bus = event_bus
        self.cache_service = cache_service
        self.environment = environment

    async def handle(self, command: RepeatMealCommand) -> Meal:
        cache_event_needed = False
        saved_meal: Meal | None = None

        async with self.uow_factory() as uow:
            source_meal = await uow.meals.find_by_id(
                command.meal_id, projection=MealProjection.FULL_WITH_TRANSLATIONS
            )
            if source_meal is None:
                raise ResourceNotFoundException(
                    f"Meal with ID {command.meal_id} not found"
                )

            if source_meal.user_id != command.user_id:
                raise AuthorizationException(
                    "You do not have permission to repeat this meal"
                )

            if source_meal.meal_type == "hydration":
                raise ValidationException(
                    "Hydration entries cannot be repeated as meals"
                )

            is_active = source_meal.status == MealStatus.READY
            is_fav = await uow.favorite_meals.is_favorite(
                command.user_id, command.meal_id
            )
            if not (is_active or is_fav):
                raise ValidationException(
                    "Meal is neither active nor favorited and cannot be repeated"
                )

            # Claim idempotency lease
            fingerprint_data = {
                "source_meal_id": command.meal_id,
                "meal_type": command.meal_type,
            }
            fingerprint = hashlib.sha256(
                json.dumps(fingerprint_data, sort_keys=True).encode("utf-8")
            ).hexdigest()

            reservation = await uow.meal_write_operations.reserve(
                user_id=command.user_id,
                operation="meal_repeat",
                idempotency_key=command.idempotency_key,
                request_fingerprint=fingerprint,
            )

            if reservation and reservation.state == "replay":
                target_id = reservation.target_meal_id
                if not target_id:
                    raise ValidationException(
                        "Idempotent meal result is no longer available",
                        error_code="IDEMPOTENCY_RESULT_UNAVAILABLE",
                    )
                replayed_meal = await uow.meals.find_by_id(
                    target_id, projection=MealProjection.FULL_WITH_TRANSLATIONS
                )
                if replayed_meal is None:
                    raise ValidationException(
                        "Idempotent meal result is no longer available",
                        error_code="IDEMPOTENCY_RESULT_UNAVAILABLE",
                    )
                return replayed_meal

            try:
                # Clone source meal aggregate
                new_meal_id = str(uuid.uuid4())
                now = utc_now()
                target_meal_type = command.meal_type or source_meal.meal_type

                new_image = (
                    MealImage(
                        image_id=str(uuid.uuid4()),
                        format=source_meal.image.format,
                        size_bytes=source_meal.image.size_bytes,
                        width=source_meal.image.width,
                        height=source_meal.image.height,
                        url=source_meal.image.url,
                    )
                    if source_meal.image
                    else None
                )

                new_food_items: list[FoodItem] = []
                if source_meal.nutrition and source_meal.nutrition.food_items:
                    for item in source_meal.nutrition.food_items:
                        new_macros = (
                            Macros(
                                protein=item.macros.protein,
                                carbs=item.macros.carbs,
                                fat=item.macros.fat,
                                fiber=item.macros.fiber,
                                sugar=item.macros.sugar,
                            )
                            if item.macros
                            else Macros(protein=0, carbs=0, fat=0, fiber=0, sugar=0)
                        )
                        allowed_units_copy = (
                            list(getattr(item, "allowed_units", []) or [])
                            if getattr(item, "allowed_units", None)
                            else None
                        )
                        new_item = FoodItem(
                            id=str(uuid.uuid4()),
                            name=item.name,
                            quantity=item.quantity,
                            unit=item.unit,
                            macros=new_macros,
                            micros=item.micros,
                            confidence=item.confidence,
                            fdc_id=getattr(item, "fdc_id", None),
                            food_reference_id=getattr(item, "food_reference_id", None),
                            is_custom=getattr(item, "is_custom", False),
                            allowed_units=allowed_units_copy,
                            nutrition_override=item.nutrition_override,
                            source_kind=getattr(item, "source_kind", None),
                            source_food_id=getattr(item, "source_food_id", None),
                            nutrition_contract_version=getattr(
                                item, "nutrition_contract_version", None
                            ),
                            source_snapshot=getattr(item, "source_snapshot", None),
                        )
                        new_food_items.append(new_item)

                new_nutrition = None
                if source_meal.nutrition:
                    nutrition_macros = (
                        Macros(
                            protein=source_meal.nutrition.macros.protein,
                            carbs=source_meal.nutrition.macros.carbs,
                            fat=source_meal.nutrition.macros.fat,
                            fiber=source_meal.nutrition.macros.fiber,
                            sugar=source_meal.nutrition.macros.sugar,
                        )
                        if source_meal.nutrition.macros
                        else Macros(protein=0, carbs=0, fat=0, fiber=0, sugar=0)
                    )
                    new_nutrition = Nutrition(
                        macros=nutrition_macros,
                        micros=source_meal.nutrition.micros,
                        food_items=new_food_items if new_food_items else None,
                        confidence_score=source_meal.nutrition.confidence_score,
                        nutrition_override=source_meal.nutrition.nutrition_override,
                    )

                cloned_meal = Meal(
                    meal_id=new_meal_id,
                    user_id=command.user_id,
                    status=MealStatus.READY,
                    created_at=now,
                    ready_at=now,
                    updated_at=now,
                    image=new_image,
                    dish_name=source_meal.dish_name,
                    emoji=source_meal.emoji,
                    meal_type=target_meal_type,
                    nutrition=new_nutrition,
                    source="repeat",
                    catalog_meal_id=source_meal.catalog_meal_id,
                    food_label_metadata=source_meal.food_label_metadata,
                    translations=source_meal.translations,
                    description=source_meal.description,
                    instructions=source_meal.instructions,
                )

                saved_meal = await uow.meals.insert(cloned_meal)
                cache_event_needed = True

                if reservation:
                    await uow.meal_write_operations.complete(
                        reservation,
                        target_meal_id=saved_meal.meal_id,
                        response={"meal_id": saved_meal.meal_id},
                    )
            except Exception:
                if reservation:
                    await uow.meal_write_operations.release(reservation)
                raise

        if cache_event_needed and saved_meal is not None:
            meal_date = (saved_meal.created_at or utc_now()).date()
            if self.event_publisher:
                await publish_meal_event(
                    self.event_publisher,
                    saved_meal,
                    event_type="created",
                    environment=self.environment,
                    meal_date=meal_date,
                    language=command.language,
                    event_bus=self.event_bus,
                    source="meal_repeat",
                )
            if self.cache_service:
                await self.cache_service.increment_revision(command.user_id)

        return saved_meal

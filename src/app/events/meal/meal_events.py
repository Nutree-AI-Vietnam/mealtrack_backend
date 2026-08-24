"""Meal domain integration events emitted to external consumers."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.app.events.integration_event import IntegrationEvent
from src.domain.model.meal import Meal
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


class MealCreatedEvent(IntegrationEvent):
    """Published after a meal is created."""

    event_type: Literal["meal.created.v1"] = "meal.created.v1"
    aggregate_type: Literal["meal"] = "meal"


class MealUpdatedEvent(IntegrationEvent):
    """Published after a meal is edited or updated."""

    event_type: Literal["meal.updated.v1"] = "meal.updated.v1"
    aggregate_type: Literal["meal"] = "meal"


class MealDeletedEvent(IntegrationEvent):
    """Published after a meal is deleted."""

    event_type: Literal["meal.deleted.v1"] = "meal.deleted.v1"
    aggregate_type: Literal["meal"] = "meal"


class MealInsightNutrition(BaseModel):
    """Nutrition snapshot embedded in a meal integration event."""

    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float
    sugar_g: float
    confidence_score: float | None = None


class MealInsightIngredient(BaseModel):
    """Ingredient snapshot embedded in a meal integration event."""

    id: str
    name: str
    quantity: float
    unit: str
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float
    sugar_g: float
    confidence: float | None = None


class MealInsightSnapshot(BaseModel):
    """Bounded meal snapshot consumed by the Worker business handler."""

    dish_name: str | None = None
    language: str = "en"
    nutrition: MealInsightNutrition
    ingredients: list[MealInsightIngredient] = Field(default_factory=list)
    user_context: dict[str, Any] | None = None
    tokens: list[str] | None = None

    @classmethod
    def from_meal(
        cls,
        meal: Meal,
        *,
        language: str = "en",
        user_context: dict[str, Any] | None = None,
        tokens: list[str] | None = None,
    ) -> MealInsightSnapshot:
        """Build the bounded Worker input from the authoritative meal."""
        if meal.nutrition is None:
            raise ValueError("Meal insight events require nutrition")

        nutrition = meal.nutrition
        macros = nutrition.effective_macros
        ingredients = [
            MealInsightIngredient(
                id=str(item.id),
                name=item.name,
                quantity=float(item.quantity),
                unit=item.unit,
                calories=float(item.calories),
                protein_g=float(item.effective_macros.protein),
                carbs_g=float(item.effective_macros.carbs),
                fat_g=float(item.effective_macros.fat),
                fiber_g=float(item.effective_macros.fiber),
                sugar_g=float(item.effective_macros.sugar),
                confidence=float(item.confidence),
            )
            for item in (nutrition.food_items or [])[:8]
        ]
        return cls(
            dish_name=meal.dish_name,
            language=(language or "en").split("-")[0].lower(),
            nutrition=MealInsightNutrition(
                calories=float(nutrition.calories),
                protein_g=float(macros.protein),
                carbs_g=float(macros.carbs),
                fat_g=float(macros.fat),
                fiber_g=float(macros.fiber),
                sugar_g=float(macros.sugar),
                confidence_score=float(nutrition.confidence_score),
            ),
            ingredients=ingredients,
            user_context=user_context,
            tokens=tokens,
        )


def meal_insight_occurred_at(meal: Meal) -> datetime:
    """Return the stable meal snapshot timestamp shared by both Queue events."""
    for attribute in ("updated_at", "ready_at", "created_at"):
        value = getattr(meal, attribute, None)
        if isinstance(value, datetime):
            return value
    return utc_now()


async def publish_meal_event(
    publisher: Any,
    meal: Meal,
    *,
    event_type: Literal["created", "updated"],
    environment: str,
    meal_date: date | datetime,
    user_id: str | None = None,
    language: str = "en",
    event_bus: Any | None = None,
    old_meal_date: date | datetime | None = None,
    source: str = "meal_write",
) -> bool:
    """Publish one committed meal integration event to the external Queue consumer."""
    if publisher is None:
        return False

    try:
        data: dict[str, Any] = {
            "user_id": str(user_id or getattr(meal, "user_id", "")),
            "meal_id": str(meal.meal_id),
            "meal_date": meal_date.isoformat(),
            "language": language or getattr(meal, "language", "en") or "en",
        }
        if old_meal_date is not None and old_meal_date != meal_date:
            data["old_meal_date"] = old_meal_date.isoformat()

        event_class = MealCreatedEvent if event_type == "created" else MealUpdatedEvent
        event = event_class(
            environment=environment,
            aggregate_id=str(meal.meal_id),
            occurred_at=meal_insight_occurred_at(meal),
            data=data,
        )
        await publisher.publish(event.to_payload())
        return True
    except Exception as exc:
        logger.error(
            "Failed to publish meal event source=%s meal_id=%s error=%s",
            source,
            getattr(meal, "meal_id", None),
            type(exc).__name__,
        )
        return False

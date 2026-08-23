"""Tracked (catalog-linked) food items must not gate meal-translation completeness."""

from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.mappers.meal_locale_ensure import ensure_requested_meal_translation
from src.app.queries.meal import GetMealByIdQuery
from src.domain.model.meal import (
    FoodItemTranslation,
    Meal,
    MealImage,
    MealStatus,
    MealTranslation,
)
from src.domain.model.nutrition import FoodItem, Macros, Nutrition


class _EventBus:
    def __init__(self, *meals: Meal):
        self._meals = list(meals)
        self.queries = []

    async def send(self, query):
        self.queries.append(query)
        return self._meals.pop(0)


def _meal_with_tracked_and_untracked_items() -> Meal:
    now = datetime.utcnow()
    return Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=now,
        ready_at=now,
        image=MealImage(image_id=str(uuid4()), format="jpeg", size_bytes=1),
        dish_name="Mixed bowl",
        nutrition=Nutrition(
            macros=Macros(protein=31, carbs=40, fat=10),
            food_items=[
                FoodItem(
                    id="tracked-item",
                    name="Grilled chicken",
                    quantity=150,
                    unit="g",
                    macros=Macros(protein=31, carbs=0, fat=3.6),
                    food_reference_id=42,
                ),
                FoodItem(
                    id="untracked-item",
                    name="Homemade sauce",
                    quantity=30,
                    unit="g",
                    macros=Macros(protein=0, carbs=40, fat=6.4),
                ),
            ],
        ),
        instructions=None,
    )


@pytest.mark.asyncio
async def test_ensure_requested_translation_excludes_tracked_items_from_provider_call():
    """translate_meal only receives untracked lines; tracked ones skip overlay."""
    meal = _meal_with_tracked_and_untracked_items()
    event_bus = _EventBus()
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock(return_value=None)
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    await ensure_requested_meal_translation(
        meal=meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    translation_service.translate_meal.assert_awaited_once()
    _, kwargs = translation_service.translate_meal.await_args
    sent_food_items = kwargs["food_items"]
    assert [item.id for item in sent_food_items] == ["untracked-item"]


@pytest.mark.asyncio
async def test_ensure_requested_translation_cache_ignores_tracked_item_count():
    """A cached translation covering only the untracked line still counts as complete."""
    meal = _meal_with_tracked_and_untracked_items()
    meal.translations = {
        "vi": MealTranslation(
            meal_id=meal.meal_id,
            language="vi",
            dish_name="Bát trộn",
            food_items=[
                FoodItemTranslation(food_item_id="untracked-item", name="Nước sốt nhà làm")
            ],
            meal_ingredients=["Nước sốt nhà làm"],
            meal_instruction=None,
        )
    }
    event_bus = _EventBus()
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock()
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    result = await ensure_requested_meal_translation(
        meal=meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result is meal
    translation_service.translate_meal.assert_not_awaited()
    assert event_bus.queries == []

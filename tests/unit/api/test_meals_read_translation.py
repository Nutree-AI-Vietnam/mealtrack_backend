import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.routes.v1 import meals_read
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


def _meal(*, translations=None) -> Meal:
    now = datetime.utcnow()
    return Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=now,
        ready_at=now,
        image=MealImage(image_id=str(uuid4()), format="jpeg", size_bytes=1),
        dish_name="Grilled chicken",
        nutrition=Nutrition(
            macros=Macros(protein=31, carbs=0, fat=3.6),
            food_items=[
                FoodItem(
                    id=str(uuid4()),
                    name="Chicken breast",
                    quantity=150,
                    unit="g",
                    macros=Macros(protein=31, carbs=0, fat=3.6),
                )
            ],
        ),
        instructions=["Grill the chicken"],
        translations=translations,
    )


def _translation(meal: Meal) -> MealTranslation:
    return MealTranslation(
        meal_id=meal.meal_id,
        language="vi",
        dish_name="Gà nướng",
        food_items=[
            FoodItemTranslation(
                food_item_id=meal.nutrition.food_items[0].id,
                name="Ức gà",
            )
        ],
        meal_ingredients=["Ức gà"],
        meal_instruction=[{"instruction": "Nướng gà", "duration_minutes": None}],
    )


@pytest.mark.asyncio
async def test_ensure_requested_translation_materializes_missing_locale():
    english_meal = _meal()
    vietnamese_translation = _translation(english_meal)
    vietnamese_meal = replace(
        english_meal,
        translations={"vi": vietnamese_translation},
    )
    event_bus = _EventBus(vietnamese_meal)
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock(return_value=vietnamese_translation)
    query = GetMealByIdQuery(meal_id=english_meal.meal_id, user_id=english_meal.user_id)

    result = await meals_read._ensure_requested_meal_translation(
        meal=english_meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result is vietnamese_meal
    translation_service.translate_meal.assert_awaited_once_with(
        meal=english_meal,
        dish_name="Grilled chicken",
        food_items=english_meal.nutrition.food_items,
        target_language="vi",
        instructions=["Grill the chicken"],
    )
    assert event_bus.queries == [query]


@pytest.mark.asyncio
async def test_ensure_requested_translation_keeps_persisted_image_names():
    meal = _meal()
    meal.source = "scanner"
    event_bus = _EventBus()
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock()
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    result = await meals_read._ensure_requested_meal_translation(
        meal=meal,
        language="de",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result is meal
    translation_service.translate_meal.assert_not_awaited()
    assert event_bus.queries == []


@pytest.mark.asyncio
async def test_ensure_requested_translation_keeps_persisted_food_label_names():
    meal = _meal()
    meal.source = "food_label"
    event_bus = _EventBus()
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock()
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    result = await meals_read._ensure_requested_meal_translation(
        meal=meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result is meal
    translation_service.translate_meal.assert_not_awaited()
    assert event_bus.queries == []


@pytest.mark.asyncio
async def test_ensure_requested_translation_does_not_call_provider_for_current_cache():
    meal = _meal()
    meal_translation = _translation(meal)
    meal.translations = {"vi": meal_translation}
    event_bus = _EventBus()
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock()
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    result = await meals_read._ensure_requested_meal_translation(
        meal=meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result is meal
    translation_service.translate_meal.assert_not_awaited()
    assert event_bus.queries == []


@pytest.mark.asyncio
async def test_ensure_requested_translation_skips_provider_for_same_call_locale():
    meal = _meal()
    meal.raw_gpt_json = json.dumps(
        {
            "dish_name": "Grilled chicken",
            "localized_language": "vi",
            "localized_dish_name": "Gà nướng",
            "foods": [
                {
                    "name": "Chicken breast",
                    "localized_name": "Ức gà",
                }
            ],
        }
    )
    event_bus = _EventBus()
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock()
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    result = await meals_read._ensure_requested_meal_translation(
        meal=meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result is meal
    translation_service.translate_meal.assert_not_awaited()
    assert event_bus.queries == []


@pytest.mark.asyncio
async def test_ensure_requested_translation_does_not_serve_incomplete_cache():
    meal = _meal(
        translations={
            "vi": MealTranslation(
                meal_id="unused",
                language="vi",
                dish_name="Gà nướng",
                food_items=[],
                meal_ingredients=[""],
                meal_instruction=[
                    {"instruction": "Nướng gà", "duration_minutes": None}
                ],
            )
        }
    )
    event_bus = _EventBus()
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock(return_value=None)
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    result = await meals_read._ensure_requested_meal_translation(
        meal=meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result.translations is None
    assert result.dish_name == meal.dish_name
    translation_service.translate_meal.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_requested_translation_reloads_identity_locale_row():
    food_id = str(uuid4())
    now = datetime.utcnow()
    meal = Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=now,
        ready_at=now,
        image=MealImage(image_id=str(uuid4()), format="jpeg", size_bytes=1),
        dish_name="Cơm tấm",
        nutrition=Nutrition(
            macros=Macros(protein=12, carbs=70, fat=4),
            food_items=[
                FoodItem(
                    id=food_id,
                    name="Bì heo",
                    quantity=1,
                    unit="phần",
                    macros=Macros(protein=6, carbs=2, fat=8),
                )
            ],
        ),
        translations={
            "vi": MealTranslation(
                meal_id="unused",
                language="vi",
                dish_name="Broken rice",
                food_items=[],
                meal_ingredients=[""],
                meal_instruction=None,
            )
        },
    )
    identity = MealTranslation(
        meal_id=meal.meal_id,
        language="vi",
        dish_name="Cơm tấm",
        food_items=[
            FoodItemTranslation(food_item_id=food_id, name="Bì heo"),
        ],
        meal_ingredients=["Bì heo"],
        meal_instruction=None,
    )
    reloaded = replace(meal, translations={"vi": identity})
    event_bus = _EventBus(reloaded)
    translation_service = type("TranslationService", (), {})()
    translation_service.translate_meal = AsyncMock(return_value=identity)
    query = GetMealByIdQuery(meal_id=meal.meal_id, user_id=meal.user_id)

    result = await meals_read._ensure_requested_meal_translation(
        meal=meal,
        language="vi",
        query=query,
        event_bus=event_bus,
        meal_translation_service=translation_service,
    )

    assert result is reloaded
    assert result.translations["vi"].dish_name == "Cơm tấm"
    translation_service.translate_meal.assert_awaited_once()
    assert event_bus.queries == [query]

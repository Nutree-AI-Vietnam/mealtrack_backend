"""Unit tests for FixMealResultCommandHandler guardrails."""

from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.meal.fix_meal_result_command import FixMealResultCommand
from src.app.handlers.command_handlers.fix_meal_result_command_handler import (
    FixMealResultCommandHandler,
    append_fix_result_correction,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition


def _meal(*, calories_macros=(30.0, 40.0, 10.0), source="scanner") -> Meal:
    protein, carbs, fat = calories_macros
    item = FoodItem(
        id=str(uuid4()),
        name="Chicken",
        quantity=120,
        unit="g",
        macros=Macros(protein=protein, carbs=carbs, fat=fat),
        confidence=0.5,
    )
    return Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=datetime(2025, 1, 15, 12, 0),
        ready_at=datetime(2025, 1, 15, 12, 1),
        image=MealImage(
            image_id=str(uuid4()),
            format="jpeg",
            size_bytes=1024,
            url="https://cdn.example/meal.jpg",
        ),
        dish_name="Chicken bowl",
        nutrition=Nutrition(
            macros=Macros(protein=protein, carbs=carbs, fat=fat),
            food_items=[item],
            confidence_score=0.5,
        ),
        source=source,
    )


@pytest.mark.asyncio
async def test_handler_rejects_calorie_jump_without_saving():
    meal = _meal()
    uow = AsyncMock()
    uow.meals.find_by_id = AsyncMock(return_value=meal)
    uow.meals.save = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    vision = AsyncMock()
    vision.analyze_with_strategy = AsyncMock(
        return_value={"structured_data": {"is_food": True}}
    )

    huge_item = FoodItem(
        id=str(uuid4()),
        name="Chicken",
        quantity=120,
        unit="g",
        macros=Macros(protein=400, carbs=400, fat=400),
        confidence=0.9,
    )
    parser = MagicMock()
    parser.parse_is_food.return_value = True
    parser.parse_to_nutrition.return_value = Nutrition(
        macros=Macros(protein=400, carbs=400, fat=400),
        food_items=[huge_item],
        confidence_score=0.9,
    )
    parser.parse_dish_name.return_value = "Chicken bowl"

    handler = FixMealResultCommandHandler(
        uow_factory=lambda: uow,
        vision_service=vision,
        gpt_parser=parser,
        download_image_bytes=AsyncMock(return_value=b"img"),
    )

    with pytest.raises(ValidationException) as exc:
        await handler.handle(
            FixMealResultCommand(
                meal_id=meal.meal_id,
                user_id=meal.user_id,
                correction="it was chicken",
            )
        )

    assert exc.value.error_code == "FIX_RESULT_CALORIE_JUMP"
    uow.meals.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_rejects_food_label_source():
    meal = _meal(source="food_label")
    uow = AsyncMock()
    uow.meals.find_by_id = AsyncMock(return_value=meal)
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    handler = FixMealResultCommandHandler(
        uow_factory=lambda: uow,
        vision_service=AsyncMock(),
        gpt_parser=MagicMock(),
        download_image_bytes=AsyncMock(return_value=b"img"),
    )

    with pytest.raises(ValidationException) as exc:
        await handler.handle(
            FixMealResultCommand(
                meal_id=meal.meal_id,
                user_id=meal.user_id,
                correction="wrong calories",
            )
        )

    assert exc.value.error_code == "FIX_RESULT_UNSUPPORTED_SOURCE"


def test_append_fix_result_correction_keeps_history():
    import json

    meal = _meal()
    meal = meal.mark_edited(
        nutrition=meal.nutrition, dish_name=meal.dish_name or "Meal"
    )
    updated = append_fix_result_correction(
        meal,
        "it was fish not chicken",
        wrong_dish="Chicken bowl",
        right_dish="Fish bowl",
        wrong_names=["Chicken", "Rice"],
        right_names=["Fish", "Rice"],
    )
    payload = json.loads(updated.raw_gpt_json or "{}")
    entry = payload["_fix_result_corrections"][-1]
    assert entry["correction"] == "it was fish not chicken"
    assert entry["wrong_dish"] == "Chicken bowl"
    assert entry["right_dish"] == "Fish bowl"
    assert entry["wrong_names"] == ["Chicken", "Rice"]
    assert entry["edit_count_after"] == meal.edit_count


def _vietnamese_meal() -> Meal:
    # Scans for Vietnamese users store the localized names on the items.
    chicken = FoodItem(
        id=str(uuid4()),
        name="Ức gà nướng",
        quantity=120,
        unit="g",
        macros=Macros(protein=36, carbs=0, fat=4),
        confidence=0.6,
    )
    rice = FoodItem(
        id=str(uuid4()),
        name="Cơm trắng",
        quantity=150,
        unit="g",
        macros=Macros(protein=4, carbs=42, fat=0.5),
        confidence=0.8,
    )
    return replace(
        _meal(),
        dish_name="Cơm gà nướng",
        nutrition=Nutrition(
            macros=Macros(protein=40, carbs=42, fat=4.5),
            food_items=[chicken, rice],
            confidence_score=0.6,
        ),
    )


def _fish_and_rice_parser() -> MagicMock:
    # The parser returns the canonical English names the vision prompt asks for.
    fish = FoodItem(
        id=str(uuid4()),
        name="grilled fish",
        quantity=120,
        unit="g",
        macros=Macros(protein=30, carbs=0, fat=5),
        confidence=0.9,
    )
    rice = FoodItem(
        id=str(uuid4()),
        name="white rice",
        quantity=150,
        unit="g",
        macros=Macros(protein=4, carbs=42, fat=0.5),
        confidence=0.9,
    )
    parser = MagicMock()
    parser.parse_is_food.return_value = True
    parser.parse_to_nutrition.return_value = Nutrition(
        macros=Macros(protein=34, carbs=42, fat=5.5),
        food_items=[fish, rice],
        confidence_score=0.9,
    )
    parser.parse_dish_name.return_value = "Grilled fish with rice"
    return parser


def _fix_handler(meal: Meal, structured_data: dict):
    uow = AsyncMock()
    uow.meals.find_by_id = AsyncMock(return_value=meal)
    uow.meals.save = AsyncMock(side_effect=lambda saved: saved)
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    vision = AsyncMock()
    vision.analyze_with_strategy = AsyncMock(
        return_value={"structured_data": structured_data}
    )
    handler = FixMealResultCommandHandler(
        uow_factory=lambda: uow,
        vision_service=vision,
        gpt_parser=_fish_and_rice_parser(),
        download_image_bytes=AsyncMock(return_value=b"img"),
    )
    return uow, handler


@pytest.fixture
def no_meal_events(monkeypatch):
    monkeypatch.setattr(
        "src.app.handlers.command_handlers.fix_meal_result_command_handler"
        ".publish_meal_event",
        AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_meal_events")
async def test_handler_keeps_vietnamese_names_after_fix():
    meal = _vietnamese_meal()
    rice_id = meal.nutrition.food_items[1].id
    _, handler = _fix_handler(
        meal,
        {
            "is_food": True,
            "dish_name": "Grilled fish with rice",
            "localized_language": "vi",
            "localized_dish_name": "Cơm cá nướng",
            "foods": [
                {"name": "grilled fish", "localized_name": "Cá nướng"},
                {"name": "white rice", "localized_name": "Cơm trắng"},
            ],
        },
    )

    saved = await handler.handle(
        FixMealResultCommand(
            meal_id=meal.meal_id,
            user_id=meal.user_id,
            correction="cá nướng, không phải gà",
            language="vi",
        )
    )

    assert [item.name for item in saved.nutrition.food_items] == [
        "Cá nướng",
        "Cơm trắng",
    ]
    assert saved.nutrition.food_items[1].id == rice_id
    assert saved.dish_name == "Cơm cá nướng"


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_meal_events")
async def test_handler_does_not_save_english_names_for_a_vietnamese_fix():
    meal = _vietnamese_meal()
    uow, handler = _fix_handler(
        meal,
        {
            "is_food": True,
            "dish_name": "Grilled fish with rice",
            "foods": [{"name": "grilled fish"}, {"name": "white rice"}],
        },
    )

    with pytest.raises(ValidationException) as exc:
        await handler.handle(
            FixMealResultCommand(
                meal_id=meal.meal_id,
                user_id=meal.user_id,
                correction="cá nướng, không phải gà",
                language="vi",
            )
        )

    assert exc.value.error_code == "FIX_RESULT_PARSE_FAILED"
    uow.meals.save.assert_not_awaited()

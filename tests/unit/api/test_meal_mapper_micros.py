"""Meal mapper micros, per-item NRF, and daily highlight amounts."""

import uuid
from datetime import datetime

from src.api.mappers.meal_mapper import MealMapper
from src.domain.services.meal_nrf_fields import meal_nrf_fields
from src.domain.model import FoodItem, Macros, Meal, MealImage, MealStatus, Nutrition
from src.domain.model.nutrition.micros import Micros
from src.domain.services.nrf_score import nrf_progress_fields, nrf_quality


def _meal_with_item(*, micros: Micros | None) -> Meal:
    item = FoodItem(
        id="item-1",
        name="Broth",
        quantity=400,
        unit="g",
        macros=Macros(protein=34, carbs=62, fat=22, fiber=8.8),
        micros=micros,
    )
    return Meal(
        meal_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        status=MealStatus.READY,
        image=MealImage(
            url="https://example.com/m.jpg",
            image_id=str(uuid.uuid4()),
            format="jpeg",
            size_bytes=10,
            width=10,
            height=10,
        ),
        dish_name="Bun bo Hue",
        created_at=datetime(2025, 1, 15, 12, 40),
        ready_at=datetime(2025, 1, 15, 12, 41),
        nutrition=Nutrition(
            macros=Macros(protein=34, carbs=62, fat=22, fiber=8.8),
            food_items=[item],
        ),
    )


def test_detailed_response_emits_item_micros_and_per_item_nrf():
    meal = _meal_with_item(micros=Micros(iron=7.6, sodium=1400))
    result = MealMapper.to_detailed_response(meal)

    assert result.food_items[0].nutrition is not None
    assert result.food_items[0].nutrition.micros == {"iron": 7.6, "sodium": 1400}
    expected = nrf_quality(34, 8.8, Micros(iron=7.6, sodium=1400))
    assert result.nrf_coverage == 2
    assert result.nrf_quality == expected
    assert result.nrf_quality != 0
    progress = nrf_progress_fields(34, 8.8, Micros(iron=7.6, sodium=1400))
    assert progress["nrf_quality"] == 0.0
    assert meal_nrf_fields(meal)["nrf_quality"] == expected


def test_detailed_response_omits_micros_and_quality_when_missing():
    meal = _meal_with_item(micros=None)
    result = MealMapper.to_detailed_response(meal)

    assert result.food_items[0].nutrition is not None
    assert result.food_items[0].nutrition.micros is None
    assert result.nrf_quality is None
    assert result.nrf_coverage == 0


def test_map_nutrition_from_dict_parses_full_micros():
    result = MealMapper.map_nutrition_from_dict(
        {
            "protein_g": 8,
            "carbs_g": 12,
            "fat_g": 8,
            "iron_mg": 0,
            "sodium_mg": 105,
            "potassium_mg": 322,
        }
    )
    assert result.micros is not None
    assert result.micros.iron == 0
    assert result.micros.sodium == 105
    assert result.micros.potassium == 322


def test_map_food_item_from_dict_parses_nested_micros():
    result = MealMapper.map_food_item_from_dict(
        {
            "id": "drink-1",
            "name": "Milk",
            "quantity": 250,
            "unit": "ml",
            "nutrition": {
                "protein_g": 8,
                "carbs_g": 12,
                "fat_g": 8,
                "sodium_mg": 105,
            },
        }
    )
    assert result.micros is not None
    assert result.micros.sodium == 105


def test_daily_nutrition_highlights_none_when_missing():
    result = MealMapper.to_daily_nutrition_response(
        {
            "date": "2025-01-15",
            "target_calories": 2000.0,
            "target_macros": {"protein": 150.0, "carbs": 250.0, "fat": 67.0},
            "total_calories": 500.0,
            "total_protein": 30.0,
            "total_carbs": 40.0,
            "total_fat": 10.0,
        }
    )
    assert result.iron_mg is None
    assert result.potassium_mg is None
    assert result.sodium_mg is None
    assert result.added_sugar_g is None


def test_daily_nutrition_highlights_preserve_zero():
    result = MealMapper.to_daily_nutrition_response(
        {
            "date": "2025-01-15",
            "target_calories": 2000.0,
            "target_macros": {"protein": 150.0, "carbs": 250.0, "fat": 67.0},
            "total_calories": 500.0,
            "total_protein": 30.0,
            "total_carbs": 40.0,
            "total_fat": 10.0,
            "iron_mg": 0.0,
            "potassium_mg": None,
            "sodium_mg": 105.0,
            "added_sugar_g": 0.0,
        }
    )
    assert result.iron_mg == 0.0
    assert result.potassium_mg is None
    assert result.sodium_mg == 105.0
    assert result.added_sugar_g == 0.0


def test_daily_nutrition_includes_dri_targets_from_profile():
    result = MealMapper.to_daily_nutrition_response(
        {
            "date": "2025-01-15",
            "target_calories": 1947.0,
            "target_macros": {"protein": 150.0, "carbs": 250.0, "fat": 67.0},
            "total_calories": 500.0,
            "total_protein": 30.0,
            "total_carbs": 40.0,
            "total_fat": 10.0,
            "gender": "male",
            "age": 34,
        }
    )
    assert result.micro_targets is not None
    assert result.micro_targets.iron_mg == 8.0
    assert result.micro_targets.potassium_mg == 3400.0
    assert result.micro_targets.sodium_mg == 2300.0
    assert result.micro_targets.fiber_g == 27.3
    assert result.micro_targets.added_sugar_g == 48.7

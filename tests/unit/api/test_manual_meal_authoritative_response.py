from datetime import UTC, datetime
from uuid import uuid4

from src.api.mappers.meal_mapper import MealMapper
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition


def test_meal_detail_uses_persisted_source_snapshot_without_reference_lookup():
    snapshot = {
        "origin": "local",
        "source_namespace": "catalog",
        "source_food_id": "42",
        "basis": "100g",
        "calories_per_100g": 124.7,
        "protein_per_100g": 2.7,
        "carbs_per_100g": 28.0,
        "fat_per_100g": 0.3,
        "fiber_per_100g": 0.4,
        "sugar_per_100g": 0.1,
        "serving_options": [{"unit": "g", "gram_weight": 1.0}],
    }
    item = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28.0, fat=0.3, fiber=0.4, sugar=0.1),
        food_reference_id=42,
        source_kind="local",
        source_food_id="42",
        nutrition_contract_version="2",
        source_snapshot=snapshot,
        serving_options=snapshot["serving_options"],
    )
    meal = Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.READY,
        created_at=datetime.now(UTC),
        ready_at=datetime.now(UTC),
        image=None,
        dish_name="Rice",
        nutrition=Nutrition(macros=item.macros, food_items=[item]),
    )

    response = MealMapper.to_detailed_response(meal)

    assert response.food_items[0].source_nutrition.calories_per_100g == 124.7
    assert response.food_items[0].source_snapshot == snapshot
    assert response.food_items[0].origin == "local"

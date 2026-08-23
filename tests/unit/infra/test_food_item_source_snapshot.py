from src.domain.model.nutrition import FoodItem, Macros
from src.infra.mappers.meal_mapper import (
    food_item_domain_to_orm,
    food_item_orm_to_domain,
)


def test_food_item_source_snapshot_round_trips_through_orm_mapping():
    item = FoodItem(
        id="item-1",
        name="Rice",
        quantity=100,
        unit="g",
        macros=Macros(protein=2.7, carbs=28, fat=0.3, fiber=0.4, sugar=0.1),
        food_reference_id=42,
        source_kind="local",
        source_food_id="42",
        nutrition_contract_version="2",
        source_snapshot={
            "basis": "100g",
            "calories_per_100g": 124.7,
            "serving_options": [{"unit": "g", "gram_weight": 1}],
        },
    )

    orm = food_item_domain_to_orm(item, nutrition_id=7)
    round_tripped = food_item_orm_to_domain(orm)

    assert round_tripped.source_kind == "local"
    assert round_tripped.source_food_id == "42"
    assert round_tripped.nutrition_contract_version == "2"
    assert round_tripped.source_snapshot["calories_per_100g"] == 124.7

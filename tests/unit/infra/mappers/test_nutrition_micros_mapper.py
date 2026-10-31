from src.domain.model.nutrition import FoodItem, Nutrition
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros
from src.infra.database.models.nutrition.nutrition import NutritionORM
from src.infra.mappers.meal_mapper import (
    nutrition_domain_to_orm,
    nutrition_orm_to_domain,
)


def test_nutrition_mapper_round_trips_micros():
    item = FoodItem(
        id="11111111-1111-1111-1111-111111111111",
        name="Milk",
        quantity=100,
        unit="g",
        macros=Macros(protein=3, carbs=5, fat=3, fiber=0),
        micros=Micros(calcium=120, sodium=40),
    )
    orm = nutrition_domain_to_orm(
        Nutrition(macros=item.macros, food_items=[item]),
        "meal-1",
    )
    assert orm.micros == {"calcium": 120, "sodium": 40}
    assert orm.food_items[0].micros == {"calcium": 120, "sodium": 40}
    loaded = nutrition_orm_to_domain(orm)
    assert loaded.micros is not None
    assert loaded.micros.calcium == 120
    assert loaded.food_items[0].micros.sodium == 40


def test_nutrition_mapper_defaults_null_confidence_score():
    orm = NutritionORM(
        meal_id="meal-legacy",
        confidence_score=None,
        protein=32.0,
        carbs=70.0,
        fat=14.0,
        fiber=3.0,
        sugar=6.0,
    )
    loaded = nutrition_orm_to_domain(orm)
    assert loaded.confidence_score == 1.0
    assert loaded.macros.protein == 32.0


def test_nutrition_mapper_preserves_explicit_zero_confidence_score():
    orm = NutritionORM(
        meal_id="meal-1",
        confidence_score=0.0,
        protein=1.0,
        carbs=1.0,
        fat=1.0,
    )
    assert nutrition_orm_to_domain(orm).confidence_score == 0.0

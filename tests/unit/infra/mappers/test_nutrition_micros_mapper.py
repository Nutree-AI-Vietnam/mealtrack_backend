from src.domain.model.nutrition import FoodItem, Nutrition
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros
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

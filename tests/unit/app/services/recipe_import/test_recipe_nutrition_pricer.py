"""Backend nutrition for imported recipes."""

import pytest
from tests.fixtures.fakes.fake_recipe_import import ingredient, wings_recipe

from src.app.services.recipe_import.recipe_nutrition_pricer import RecipeNutritionPricer
from src.domain.model.recipe_import import IngredientNutritionSource


@pytest.mark.asyncio
async def test_prices_each_line_by_weight_and_divides_by_servings(nutrition):
    priced = await RecipeNutritionPricer(nutrition).price(
        wings_recipe().ingredients, servings=2
    )

    assert nutrition.calls == [
        [
            {"name": "chicken wing, raw", "amount": 500.0, "unit": "g"},
            {"name": "fish sauce", "amount": 36.0, "unit": "g"},
            {"name": "garlic", "amount": 9, "unit": "g"},
        ]
    ]
    wings, sauce, garlic = priced.ingredients
    assert (wings.grams, wings.food_reference_id) == (500.0, 7)
    assert wings.nutrition_source == IngredientNutritionSource.FOOD_REFERENCE
    assert sauce.nutrition_source == IngredientNutritionSource.FATSECRET
    assert garlic.nutrition_source == IngredientNutritionSource.AI_ESTIMATE
    total = priced.nutrition.total
    assert total.protein == pytest.approx(90 + 1.8 + 0.6, abs=0.2)
    per = priced.nutrition.per_serving
    assert per.protein == pytest.approx(total.protein / 2, abs=0.1)
    # Calories always follow the backend formula from the macros.
    assert per.calories == pytest.approx(
        per.protein * 4 + (per.carbs - per.fiber) * 4 + per.fiber * 2 + per.fat * 9,
        abs=1.0,
    )
    assert priced.nutrition.estimated is True
    assert priced.nutrition.complete and not priced.servings_estimated


@pytest.mark.asyncio
async def test_lines_without_weight_are_listed_unless_optional(nutrition):
    items = (
        ingredient("gạo", "rice, uncooked", 200, "g"),
        ingredient("muối", "salt", None, None),
        ingredient("ngò", "cilantro", None, None, optional=True),
    )

    priced = await RecipeNutritionPricer(nutrition).price(items, servings=None)

    assert [line["name"] for line in nutrition.calls[0]] == ["rice, uncooked"]
    assert priced.nutrition.unpriced_ingredients == ("muối",)
    assert priced.ingredients[1].macros is None
    assert priced.servings_estimated
    # 200 g rice is about 700 kcal: one estimated serving of ~550 kcal.
    assert priced.nutrition.servings == 1


@pytest.mark.asyncio
async def test_nothing_to_weigh_skips_the_lookup(nutrition):
    priced = await RecipeNutritionPricer(nutrition).price(
        (ingredient("muối", "salt", None, None),), servings=4
    )

    assert nutrition.calls == []
    assert priced.nutrition.total.calories == 0
    assert priced.nutrition.servings == 4

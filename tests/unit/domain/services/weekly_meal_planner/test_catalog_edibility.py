"""The planner checks the numbers itself rather than trusting the database.

Staging publishes 920 recipes, every one of them flagged active, published
and nutrition-ready. A hundred carry no calories, a hundred and fifty-seven
are syrups and how-to posts, and not one states how many it feeds. A status
column is a claim; these rules are the measurement.
"""

from decimal import Decimal

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    MAX_SERVING_KCAL,
    MIN_SERVING_KCAL,
    is_edible_serving,
)


def _meal(protein="30", carbs="40", fat="10", fiber="5", lines=(("x", 300, "g"),)):
    return CatalogMeal(
        id="m1",
        catalog_key="m1",
        content_hash="a" * 64,
        name="Món",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal(protein),
        carbs_g=Decimal(carbs),
        fat_g=Decimal(fat),
        fiber_g=Decimal(fiber),
        meal_types=("lunch",),
        ingredients=tuple(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name=name,
                quantity=Decimal(str(grams)),
                unit=unit,
            )
            for name, grams, unit in lines
        ),
    )


def test_an_ordinary_dish_passes():
    assert is_edible_serving(_meal()) is True


def test_a_recipe_with_no_nutrition_at_all_is_not_food():
    """A hundred published rows in staging carry zero calories."""
    assert is_edible_serving(_meal(protein="0", carbs="0", fat="0", fiber="0")) is False


def test_a_serving_too_small_to_be_a_meal_is_rejected():
    assert is_edible_serving(_meal(protein="1", carbs="2", fat="0", fiber="0")) is False


def test_a_serving_nobody_could_eat_is_rejected():
    """Syrup and preserves read as thousands of calories a serving."""
    huge = _meal(protein="100", carbs="500", fat="100", lines=(("x", 300, "g"),))

    assert is_edible_serving(huge) is False


def test_food_denser_than_pure_fat_is_arithmetic_not_food():
    """Oil is about 900 kcal per 100 g; nothing edible beats it."""
    dense = _meal(protein="50", carbs="300", fat="100", lines=(("x", 200, "g"),))

    assert is_edible_serving(dense) is False


def test_density_is_not_judged_from_a_partly_weighed_recipe():
    """Rice in grams and herbs in bó must not read as impossibly dense."""
    partly = _meal(
        protein="30",
        carbs="60",
        fat="15",
        lines=(("rau", 2, "bó"), ("gạo", 60, "g")),
    )

    assert is_edible_serving(partly) is True


def test_a_pot_is_judged_on_one_serving_of_it():
    """The gate runs after the macros are divided, not before."""
    pot = _meal(protein="150", carbs="600", fat="120", lines=(("x", 1500, "g"),))

    assert is_edible_serving(pot) is True


def test_the_bounds_are_a_meal_not_a_garnish_or_a_party():
    assert MIN_SERVING_KCAL < 150
    assert MAX_SERVING_KCAL > 1200

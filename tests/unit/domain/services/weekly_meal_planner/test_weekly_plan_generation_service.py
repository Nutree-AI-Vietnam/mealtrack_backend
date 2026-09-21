from decimal import Decimal

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService


def _meal(meal_id: str, name: str, meal_types=("lunch", "dinner"), *, popularity=1):
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=meal_types,
        popularity_rank=popularity,
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name=name,
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )


def test_generation_covers_all_fourteen_weekly_coordinates_deterministically():
    meals = [_meal("tofu", "Tofu")]
    preferences = WeeklyMealPlanPreferences(diet="vegetarian")

    first = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=preferences,
    )
    second = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=preferences,
    )

    assert first == second
    assert len(first) == 14
    assert {(slot.day_index, slot.slot_index) for slot in first} == {
        (day, slot) for day in range(7) for slot in range(2)
    }
    assert {slot.recipe_id for slot in first} == {"tofu"}


def test_generation_falls_back_when_a_preference_has_no_matching_recipe():
    meals = [_meal("chicken", "Chicken Bowl")]
    preferences = WeeklyMealPlanPreferences(diet="vegetarian")

    result = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=preferences,
    )

    assert len(result) == 14
    assert all(slot.recipe_id == "chicken" for slot in result)

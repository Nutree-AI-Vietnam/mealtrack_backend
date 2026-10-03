from decimal import Decimal

import pytest

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import (
    WEEKLY_DAYS,
    WEEKLY_PLAN_SLOT_COUNT,
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlanPreferences,
)
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


def test_generation_covers_all_weekly_coordinates_deterministically():
    meals = [_meal("tofu", "Tofu", meal_types=("breakfast", "lunch", "dinner"))]
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
    assert len(first) == WEEKLY_PLAN_SLOT_COUNT
    assert {(slot.day_index, slot.slot_index) for slot in first} == {
        (day, slot)
        for day in range(WEEKLY_DAYS)
        for slot in range(WEEKLY_SLOTS_PER_DAY)
    }
    assert {slot.recipe_id for slot in first} == {"tofu"}


def test_generation_assigns_each_meal_type_to_its_slot():
    generated = WeeklyPlanGenerationService().generate(
        [
            _meal("oats", "Oats", meal_types=("breakfast",)),
            _meal("tofu", "Tofu", meal_types=("lunch",)),
            _meal("soup", "Soup", meal_types=("dinner",)),
        ],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=1800,
        preferences=WeeklyMealPlanPreferences(),
    )

    by_slot = {
        slot.slot_index: slot.recipe_id for slot in generated if slot.day_index == 0
    }
    assert by_slot == {0: "oats", 1: "tofu", 2: "soup"}
    assert len(generated) == WEEKLY_PLAN_SLOT_COUNT


def test_generation_leaves_slots_empty_when_hard_preference_has_no_match():
    meals = [_meal("chicken", "Chicken Bowl")]
    preferences = WeeklyMealPlanPreferences(diet="vegetarian")

    result = WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=preferences,
    )

    assert len(result) == WEEKLY_PLAN_SLOT_COUNT
    assert all(slot.recipe_id is None for slot in result)


@pytest.mark.parametrize(
    "name",
    [
        "Rosita cocktail",
        "Ly đá chanh bạc hà",
        "Sữa Macca",
        "Sữa đậu xanh lá dứa",
        "Chocolate tofu pudding",
        "French toast with cinnamon",
        "Bánh khoai mỡ chiên",
        "Bánh tuyết thiên sứ",
        "Bắp rang bơ",
        "Latte hoa đậu biếc",
        "Soda blue ocean",
        "Bánh con sùng nước cốt dừa",
    ],
)
def test_generation_excludes_beverages_and_desserts_from_main_meals(name):
    generated = WeeklyPlanGenerationService().generate(
        [_meal("not-a-main-meal", name)],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(),
    )

    assert all(slot.recipe_id is None for slot in generated)


@pytest.mark.parametrize(
    "name",
    [
        "Mực một nắng",
        "Tép muối xổi",
        "Sườn chiên sả ớt",
        "Cải thìa xào dầu hào",
        "Tofu with shrimp",
    ],
)
def test_vegetarian_generation_excludes_all_catalogued_seafood(name):
    generated = WeeklyPlanGenerationService().generate(
        [_meal("seafood", name)],
        user_id="user-1",
        week_start_date="2026-09-21",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(diet="vegetarian"),
    )

    assert all(slot.recipe_id is None for slot in generated)

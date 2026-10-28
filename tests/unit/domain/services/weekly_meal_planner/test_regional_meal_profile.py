from decimal import Decimal

import pytest

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.model.weekly_meal_planner.regional_meal_profile import (
    infer_meal_region,
    normalize_meal_region,
    regional_meal_profile,
)
from src.domain.services.meal_recommendation.calorie_allocation_policy import (
    CalorieAllocationPolicy,
)
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService
from src.domain.services.weekly_meal_planner.meal_practicality import (
    practicality_rank,
)


def _meal(meal_id, name, cuisine, meal_types=("breakfast", "lunch", "dinner")):
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=name,
        cuisine=cuisine,
        description=None,
        image_url=None,
        protein_g=Decimal("25"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("12"),
        fiber_g=Decimal("5"),
        meal_types=meal_types,
        popularity_rank=1,
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name=name,
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )


def _generate(meals, region):
    return WeeklyPlanGenerationService().generate(
        meals,
        user_id="user-1",
        week_start_date="2026-10-26",
        daily_calories=2000,
        preferences=WeeklyMealPlanPreferences(region=region),
    )


@pytest.mark.parametrize(
    ("language", "timezone", "expected"),
    [
        ("vi", "America/Los_Angeles", "VN"),
        ("vi-VN", None, "VN"),
        ("en", "Asia/Ho_Chi_Minh", "VN"),
        ("en", "Asia/Saigon", "VN"),
        ("en", "America/New_York", "US"),
        ("es", "America/Indiana/Indianapolis", "US"),
        ("en", "Pacific/Honolulu", "US"),
        ("en", "US/Pacific", "US"),
        ("en", "America/Toronto", None),
        ("en", "Europe/London", None),
        (None, None, None),
    ],
)
def test_infer_meal_region_prefers_vietnamese_speakers_then_timezone(
    language, timezone, expected
):
    assert infer_meal_region(language=language, timezone=timezone) == expected


def test_region_codes_normalize_and_reject_unsupported_values():
    assert normalize_meal_region(" us ") == "US"
    assert normalize_meal_region("vn") == "VN"
    assert normalize_meal_region("FR") is None
    assert normalize_meal_region(None) is None
    assert regional_meal_profile("JP") is None


def test_cuisine_rank_puts_home_food_before_familiar_food():
    us = regional_meal_profile("US")
    vn = regional_meal_profile("VN")
    assert us is not None and vn is not None

    assert us.cuisine_rank("american") == 0
    assert us.cuisine_rank("Tex-Mex") == 0
    assert us.cuisine_rank("mexican") == 1
    assert us.cuisine_rank("vietnamese") == 2
    assert vn.cuisine_rank("Việt Nam") == 0
    assert vn.cuisine_rank("korean") == 1
    assert vn.cuisine_rank("american") == 2


def test_preferences_keep_region_through_storage():
    stored = WeeklyMealPlanPreferences.from_payload({"region": "us"}).to_dict()

    assert stored["region"] == "US"
    assert WeeklyMealPlanPreferences.from_payload(stored).region == "US"
    assert WeeklyMealPlanPreferences.from_payload({"region": "fr"}).region is None
    assert WeeklyMealPlanPreferences.from_payload({}).region is None
    with pytest.raises(ValueError):
        WeeklyMealPlanPreferences(region="FR")


def test_us_region_treats_cheese_and_pancakes_as_everyday_breakfast():
    assert practicality_rank("Spinach and Cheese Omelet", "breakfast", region="US") == (
        0,
        0,
    )
    assert practicality_rank("Blueberry Pancakes", "breakfast", region="US")[0] == 0
    assert practicality_rank("Baked Salmon with Rice", "dinner", region="US") == (0, 0)
    # Without a region the shared list still treats cheese as a luxury.
    assert practicality_rank("Spinach and Cheese Omelet", "breakfast")[1] == 2


def test_vn_region_treats_pho_as_breakfast_and_keeps_salmon_a_luxury():
    assert practicality_rank("Phở bò", "breakfast", region="VN")[0] == 0
    assert practicality_rank("Phở bò", "breakfast")[0] == 1
    assert practicality_rank("Cháo cá hồi", "breakfast", region="VN")[0] == 2


def test_region_gives_each_country_its_own_week():
    meals = [
        _meal("pho", "Phở gà", "vietnamese"),
        _meal("com", "Cơm gà", "vietnamese"),
        _meal("bun", "Bún chả", "vietnamese"),
        _meal("omelet", "Veggie Omelet", "american"),
        _meal("burger", "Turkey Burger", "american"),
        _meal("chili", "Turkey Chili", "american"),
    ]

    us_week = _generate(meals, "US")
    vn_week = _generate(meals, "VN")

    assert {slot.recipe_id for slot in us_week} == {"omelet", "burger", "chili"}
    assert {slot.recipe_id for slot in vn_week} == {"pho", "com", "bun"}


def test_region_falls_back_to_familiar_cuisine_before_other_food():
    meals = [
        _meal("omelet", "Veggie Omelet", "american", meal_types=("breakfast",)),
        _meal("bibimbap", "Bibimbap", "korean", meal_types=("lunch", "dinner")),
        _meal("bun", "Bún chả", "vietnamese", meal_types=("lunch", "dinner")),
    ]

    week = _generate(meals, "US")

    by_slot = [
        {slot.recipe_id for slot in week if slot.slot_index == index}
        for index in range(3)
    ]
    # Korean is familiar in the US. Vietnamese fills dinner only because the
    # same dish is never served twice in one day.
    assert by_slot == [{"omelet"}, {"bibimbap"}, {"bun"}]


def test_no_region_keeps_the_mixed_catalog_week():
    meals = [
        _meal("pho", "Phở gà", "vietnamese"),
        _meal("omelet", "Veggie Omelet", "american"),
    ]

    assert {slot.recipe_id for slot in _generate(meals, None)} == {"pho", "omelet"}


def test_regional_calorie_split_makes_us_dinner_the_largest_meal():
    us = regional_meal_profile("US")
    assert us is not None

    split = CalorieAllocationPolicy(weights=us.slot_calorie_shares).allocate(2000)

    assert sum(split.values()) == 2000
    assert split["dinner"] > split["lunch"] > split["breakfast"]
    assert CalorieAllocationPolicy().allocate(2000) == {
        "breakfast": 500,
        "lunch": 750,
        "dinner": 750,
    }
    with pytest.raises(ValueError):
        CalorieAllocationPolicy(weights=(0.5, 0.5, 0.5))

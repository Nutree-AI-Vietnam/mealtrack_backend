"""Stable constraints retain Unicode and hard-filter behavior on compact meals."""

from dataclasses import replace
from decimal import Decimal

import pytest

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.meal_recommendation.catalog_selection_features import (
    CatalogSelectionFeatures,
)
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    _MEAT_WORDS,
    WeeklyPlanGenerationService,
    _contains_any,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("fish rice", True),
        ("shellfish rice", False),
        ("fish_rice", False),
        ("cá, rau", True),
        ("cáà", False),
        ("(thịt)", True),
        ("dầu hào và rau", True),
        ("sauce dầu hàox", False),
        ("éfish", False),
        ("fishβ", False),
        ("🐟fish🐟", True),
        ("Tofu, rice and vegetables", False),
    ],
)
def test_combined_meat_pattern_preserves_unicode_word_boundaries(text, expected):
    assert _contains_any(text, _MEAT_WORDS) is expected


def test_compact_candidate_enforces_ingredient_constraints_without_payload():
    meal = CatalogMeal(
        id="bowl",
        catalog_key="bowl",
        content_hash="a" * 64,
        name="Plain bowl",
        cuisine="Vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal(30),
        carbs_g=Decimal(40),
        fat_g=Decimal(10),
        fiber_g=Decimal(5),
        meal_types=("lunch", "dinner"),
        allergen_codes=("milk",),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name="Chicken",
                quantity=Decimal(100),
                unit="g",
            ),
        ),
    )
    compact = replace(
        meal,
        ingredients=(),
        selection_features=CatalogSelectionFeatures(
            "plain bowl vietnamese  chicken", True, False, False
        ),
    )
    generator = WeeklyPlanGenerationService()
    for preferences, expected in [
        (WeeklyMealPlanPreferences(diet="vegetarian"), False),
        (WeeklyMealPlanPreferences(allergies=("milk",)), False),
        (WeeklyMealPlanPreferences(dislikes=("chicken",)), False),
        (WeeklyMealPlanPreferences(diet="no-pork"), True),
    ]:
        assert generator.is_hard_eligible(compact, preferences) is expected
        assert generator.is_hard_eligible(meal, preferences) is expected

"""Tests for FatSecret food_description macro parsing."""

from src.domain.services.fatsecret_description_nutrition import (
    description_macros_as_100g,
    parse_fatsecret_nutrition,
)


def test_parse_skips_per_100g_prefix_digits():
    parsed = parse_fatsecret_nutrition(
        {
            "food_description": (
                "Per 100g - Calories: 155kcal | Fat: 11g | Carbs: 1.1g | Protein: 13g"
            )
        }
    )
    assert parsed == {
        "calories": 155.0,
        "fat": 11.0,
        "carbs": 1.1,
        "protein": 13.0,
    }


def test_description_macros_as_100g_requires_per_100g_hint():
    assert description_macros_as_100g(
        {
            "food_description": (
                "Per Serving - Calories: 200kcal | Fat: 8g | Carbs: 20g | Protein: 10g"
            )
        }
    ) == {}
    assert description_macros_as_100g(
        {
            "food_description": (
                "Per 100g - Calories: 155kcal | Fat: 11g | Carbs: 1.1g | Protein: 13g"
            )
        }
    )["protein_100g"] == 13.0

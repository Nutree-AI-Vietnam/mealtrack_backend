"""Unit tests for the pure food-reference display-name resolver."""

from src.api.mappers.food_reference_display_name import (
    resolve_food_reference_display_name,
)


def test_resolves_english_name_regardless_of_name_vi():
    projection = {
        "name": "Grilled chicken",
        "name_vi": "Gà nướng",
    }

    assert resolve_food_reference_display_name(projection, "en") == "Grilled chicken"


def test_empty_language_defaults_to_english():
    projection = {"name": "Grilled chicken", "name_vi": None}

    assert resolve_food_reference_display_name(projection, "") == "Grilled chicken"
    assert resolve_food_reference_display_name(projection, None) == "Grilled chicken"


def test_vi_uses_name_vi():
    projection = {
        "name": "Grilled chicken",
        "name_vi": "Gà nướng",
    }

    assert resolve_food_reference_display_name(projection, "vi") == "Gà nướng"


def test_vi_falls_back_to_english_when_name_vi_missing():
    projection = {
        "name": "Grilled chicken",
        "name_vi": None,
    }

    assert resolve_food_reference_display_name(projection, "vi") == "Grilled chicken"


def test_non_vi_language_ignores_name_vi_column():
    projection = {
        "name": "Grilled chicken",
        "name_vi": "Gà nướng",
    }

    assert resolve_food_reference_display_name(projection, "ja") == "Grilled chicken"
    assert resolve_food_reference_display_name(projection, "fr") == "Grilled chicken"

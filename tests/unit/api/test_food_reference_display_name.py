"""Unit tests for the pure food-reference display-name resolver."""

from src.api.mappers.food_reference_display_name import (
    resolve_food_reference_display_name,
    resolve_grocery_proposal_display_name,
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


def test_grocery_proposal_uses_authored_name_vi_without_translation():
    assert (
        resolve_grocery_proposal_display_name(
            {"name": "Tomato", "name_vi": "Cà chua"}, "Tomato", "vi"
        )
        == "Cà chua"
    )


def test_grocery_proposal_uses_direct_vietnamese_label_when_catalog_label_missing():
    assert (
        resolve_grocery_proposal_display_name(
            {"name": "Tomato", "name_vi": None}, "Tomato", "vi-VN"
        )
        == "Cà chua"
    )


def test_grocery_proposal_uses_localized_fallback_for_unknown_source_name():
    assert (
        resolve_grocery_proposal_display_name(None, "Dragon fruit", "vi")
        == "Nguyên liệu"
    )


def test_grocery_proposal_replaces_numeric_source_name_with_generic_label():
    assert resolve_grocery_proposal_display_name(None, "42", "vi") == "Nguyên liệu"

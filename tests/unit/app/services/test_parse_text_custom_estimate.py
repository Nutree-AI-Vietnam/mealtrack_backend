import pytest

from src.app.services.parse_text_custom_estimate import (
    apply_custom_estimate,
    custom_estimate_quantity_g,
)


def test_mass_unit_stays_in_grams():
    item = {
        "name": "Secret sauce",
        "quantity": 80,
        "unit": "g",
        "protein": 1,
        "carbs": 8,
        "fat": 4,
        "fiber": 0,
    }

    resolved = apply_custom_estimate(item)

    assert resolved is not None
    assert resolved["origin"] == "custom"
    assert resolved["data_source"] == "custom"
    assert resolved["quantity"] == 80
    assert resolved["unit"] == "g"
    assert resolved["food_reference_id"] is None
    assert {option["unit"] for option in resolved["allowed_units"]} == {"g", "kg"}


def test_large_mass_stores_as_kilograms():
    item = {
        "name": "Rice",
        "quantity": 1.5,
        "unit": "kg",
        "protein": 12,
        "carbs": 120,
        "fat": 2,
    }

    resolved = apply_custom_estimate(item)

    assert resolved is not None
    assert resolved["unit"] == "kg"
    assert resolved["quantity"] == 1.5
    assert resolved["quantity_g"] == 1500


def test_countable_unit_uses_hundred_gram_serving():
    item = {
        "name": "Sườn Nướng",
        "quantity": 1,
        "unit": "miếng",
        "english_unit": "slice",
        "protein": 18,
        "carbs": 4,
        "fat": 11,
    }

    assert custom_estimate_quantity_g(item) == 100
    resolved = apply_custom_estimate(item)
    assert resolved is not None
    assert resolved["unit"] == "g"
    assert resolved["quantity"] == 100
    assert resolved["protein"] == 18
    assert resolved["carbs"] == 4
    assert resolved["fat"] == 11


def test_impossible_density_is_rejected():
    item = {
        "name": "Potato",
        "quantity": 100,
        "quantity_g": 100,
        "unit": "g",
        "protein": 0,
        "carbs": 0,
        "fat": 101,
    }

    assert apply_custom_estimate(item) is None


@pytest.mark.parametrize("name", ["Unfamiliar seed paste", "Potato", "Apple"])
def test_custom_physical_validation_is_independent_of_food_name(name):
    item = {
        "name": name,
        "quantity": 100,
        "unit": "g",
        "protein": 1,
        "carbs": 0,
        "fat": 98.9,
    }

    resolved = apply_custom_estimate(item)

    assert resolved is not None
    assert resolved["fat"] == 98.9
    assert resolved["origin"] == "custom"


def test_custom_granola_preserves_dense_macros_and_micro_states():
    item = {
        "name": "Granola",
        "quantity": 10,
        "unit": "g",
        "protein": 1,
        "carbs": 6.5,
        "fat": 2.2,
        "fiber": 1,
        "sugar": 2,
        "portion_micros": {"iron": 0.4, "vitamin_c": None, "sodium": 0},
    }

    resolved = apply_custom_estimate(item)

    assert resolved is not None
    assert resolved["carbs"] == 6.5
    assert resolved["fat"] == 2.2
    assert resolved["portion_micros"] == {
        "iron": 0.4,
        "vitamin_c": None,
        "sodium": 0,
    }


@pytest.mark.parametrize(
    "invalid",
    [
        {"carbs": 8, "fat": 2},
        {"fiber": 7},
        {"sugar": 7},
        {"portion_micros": {"saturated_fat": 3}},
        {"portion_micros": {"added_sugar": 3}},
        {"portion_micros": {"sodium": float("inf")}},
    ],
)
def test_invalid_custom_portion_cannot_gain_saveable_provenance(invalid):
    item = {
        "name": "Granola",
        "quantity": 10,
        "unit": "g",
        "protein": 1,
        "carbs": 6,
        "fat": 2,
        "fiber": 1,
        "sugar": 2,
        **invalid,
    }

    assert apply_custom_estimate(item) is None
    assert "origin" not in item
    assert "allowed_units" not in item

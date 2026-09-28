from types import SimpleNamespace

import pytest

from src.domain.model.nutrition.extra_nutrients import (
    extra_nutrients_to_micros,
    food_item_effective_micros,
    merge_meal_micros,
)
from src.domain.model.nutrition.micros_ops import merge_micros, scale_micros


def test_extra_nutrients_aliases_and_nested_amount():
    micros = extra_nutrients_to_micros(
        {
            "calcium_mg": 100,
            "sodium_mg": {"amount": 200, "unit": "mg"},
            "saturated_fat": 3,
        },
        factor=0.5,
    )
    assert micros is not None
    assert micros.calcium == 50
    assert micros.sodium == 100
    assert micros.saturated_fat == 1.5


def test_strict_extra_nutrients_converts_supported_units_and_rejects_unknown_units():
    micros = extra_nutrients_to_micros(
        {
            "iron_mg": {"amount": 0.002, "unit": "g"},
            "vitamin_a_mcg": {"amount": 0.3, "unit": "mg"},
            "potassium_mg": {"amount": 700, "unit": "kcal"},
        },
        validate_units=True,
    )

    assert micros is not None
    assert micros.iron == pytest.approx(2.0)
    assert micros.vitamin_a == pytest.approx(300.0)
    assert micros.potassium is None


def test_strict_extra_nutrients_requires_units_on_objects_and_prefers_normalized_alias():
    micros = extra_nutrients_to_micros(
        {
            "iron": {"amount": 8.0, "unit": "mg"},
            "iron_mg": {"amount": 2.0, "unit": "mg"},
            "calcium_mg": {"amount": 99.0},
            "vitamin_d_mcg": 5.0,
        },
        validate_units=True,
    )

    assert micros is not None
    assert micros.iron == 2.0
    assert micros.calcium is None
    assert micros.vitamin_d == 5.0


def test_strict_extra_nutrients_maps_all_micros_fields():
    micros = extra_nutrients_to_micros(
        {
            "vitamin_k_mcg": 1,
            "thiamin_mg": 2,
            "riboflavin_mg": 3,
            "niacin_mg": 4,
            "vitamin_b6_mg": 5,
            "vitamin_b12_mcg": 6,
            "folate_mcg": 7,
            "phosphorus_mg": 8,
            "zinc_mg": 9,
            "selenium_mcg": 10,
        },
        validate_units=True,
    )

    assert micros is not None
    assert micros.to_dict() == {
        "vitamin_k": 1,
        "thiamin": 2,
        "riboflavin": 3,
        "niacin": 4,
        "vitamin_b6": 5,
        "vitamin_b12": 6,
        "folate": 7,
        "phosphorus": 8,
        "zinc": 9,
        "selenium": 10,
    }


def test_micros_helpers_reject_non_finite_converted_scaled_and_merged_values():
    converted_overflow = extra_nutrients_to_micros(
        {"iron_mg": {"amount": 1e308, "unit": "g"}},
        validate_units=True,
    )
    scaled_overflow = extra_nutrients_to_micros({"iron_mg": 1e308}, factor=1e308)
    merged_overflow = merge_micros(
        extra_nutrients_to_micros({"iron_mg": 1e308}),
        extra_nutrients_to_micros({"iron_mg": 1e308}),
    )

    assert converted_overflow is None
    assert scaled_overflow is None
    assert merged_overflow is None


def test_complete_micro_merge_omits_values_missing_from_any_ingredient():
    from src.domain.model.nutrition.micros_ops import merge_micros_complete

    complete = merge_micros_complete(
        extra_nutrients_to_micros({"iron_mg": 2, "sodium_mg": 100}),
        extra_nutrients_to_micros({"iron_mg": 3, "potassium_mg": 200}),
    )

    assert complete is not None
    assert complete.iron == 5
    assert complete.sodium is None
    assert complete.potassium is None


def test_merge_skips_non_micros_values():
    from unittest.mock import MagicMock

    from src.domain.model.nutrition.micros_ops import is_empty

    assert is_empty(MagicMock()) is True
    merged = merge_micros(MagicMock(), extra_nutrients_to_micros({"iron_mg": 2}))
    assert merged is not None
    assert merged.iron == 2


def test_merge_and_scale_skip_empty():
    assert merge_micros(None, None) is None
    left = extra_nutrients_to_micros({"iron_mg": 2})
    right = extra_nutrients_to_micros({"iron_mg": 3, "vitamin_c_mg": 10})
    merged = merge_micros(left, right)
    assert merged is not None
    assert merged.iron == 5
    assert merged.vitamin_c == 10
    scaled = scale_micros(merged, 2)
    assert scaled is not None
    assert scaled.iron == 10


def test_food_item_effective_micros_scales_snapshot_extras():
    item = SimpleNamespace(
        micros=None,
        quantity=150,
        unit="g",
        allowed_units=None,
        source_snapshot={
            "extra_nutrients": {
                "iron_mg": 2.0,
                "sodium_mg": 400,
                "potassium_mg": 200,
                "added_sugar_g": 8,
            }
        },
    )
    micros = food_item_effective_micros(item)
    assert micros is not None
    assert micros.iron == 3.0
    assert micros.sodium == 600
    assert micros.potassium == 300
    assert micros.added_sugar == 12


def test_extras_from_portion_micros_scales_to_100g():
    from src.domain.model.nutrition.extra_nutrients import extras_from_portion_micros

    extras = extras_from_portion_micros(
        {"iron": 1.5, "sodium": 230, "added_sugar": 6},
        150,
    )
    assert extras is not None
    assert extras["iron"] == pytest.approx(1.0)
    assert extras["sodium"] == pytest.approx(230 * 100 / 150)
    assert extras["added_sugar"] == pytest.approx(4)


def test_merge_meal_micros_reads_snapshot_when_item_micros_missing():
    item = SimpleNamespace(
        micros=None,
        quantity=100,
        unit="g",
        allowed_units=None,
        source_snapshot={"extra_nutrients": {"iron_mg": 1.8, "sodium_mg": 230}},
    )
    merged = merge_meal_micros(None, [item])
    assert merged is not None
    assert merged.iron == 1.8
    assert merged.sodium == 230

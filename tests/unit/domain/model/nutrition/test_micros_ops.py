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

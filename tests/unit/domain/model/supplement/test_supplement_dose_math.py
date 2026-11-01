"""Doses multiply per-unit supplement nutrients; nothing becomes calories."""

import math

import pytest

from src.domain.model.supplement import (
    TRACKED_NUTRIENTS,
    SupplementNutrients,
    dose,
)


def test_tracked_nutrients_are_the_19_vitamins_and_minerals():
    assert len(TRACKED_NUTRIENTS) == 19
    assert "saturated_fat" not in TRACKED_NUTRIENTS
    assert "added_sugar" not in TRACKED_NUTRIENTS


def test_one_unit_of_25_mcg_d3_is_25_mcg():
    per_unit = SupplementNutrients({"vitamin_d": 25.0})

    assert dose(per_unit, 1).to_micros().vitamin_d == 25.0


def test_two_capsules_double_every_key_and_preformed_vitamin_a():
    per_unit = SupplementNutrients(
        {"vitamin_a": 300.0, "vitamin_c": 60.0, "zinc": 7.5},
        vitamin_a_preformed=300.0,
    )

    taken = dose(per_unit, 2)

    assert taken.amounts == {"vitamin_a": 600.0, "vitamin_c": 120.0, "zinc": 15.0}
    assert taken.vitamin_a_preformed == 600.0
    micros = taken.to_micros()
    assert micros.vitamin_c == 120.0
    assert micros.iron is None


def test_fractional_doses_round_away_float_noise():
    taken = dose(SupplementNutrients({"vitamin_b12": 0.1}), 3)

    assert taken.amounts["vitamin_b12"] == 0.3


@pytest.mark.parametrize("units", [0, -1, math.inf, math.nan])
def test_a_dose_needs_a_positive_finite_number_of_units(units):
    with pytest.raises(ValueError):
        dose(SupplementNutrients({"vitamin_c": 500.0}), units)


def test_unknown_keys_and_negative_amounts_are_rejected():
    with pytest.raises(ValueError):
        SupplementNutrients({"iodine": 150.0})
    with pytest.raises(ValueError):
        SupplementNutrients({"vitamin_c": -1.0})


def test_preformed_vitamin_a_cannot_exceed_total_vitamin_a():
    with pytest.raises(ValueError):
        SupplementNutrients({"vitamin_a": 100.0}, vitamin_a_preformed=200.0)
    with pytest.raises(ValueError):
        SupplementNutrients({}, vitamin_a_preformed=10.0)


def test_preformed_a_hair_above_the_total_is_capped_and_survives_a_larger_dose():
    per_unit = SupplementNutrients({"vitamin_a": 100.0}, vitamin_a_preformed=100.00005)

    assert per_unit.vitamin_a_preformed == 100.0
    assert dose(per_unit, 3).vitamin_a_preformed == 300.0


def test_json_round_trip_keeps_preformed_and_drops_untracked_keys():
    stored = {"vitamin_a": 900.0, "vitamin_a_preformed": 450.0, "iodine": 150.0}

    nutrients = SupplementNutrients.from_json(stored)

    assert nutrients.amounts == {"vitamin_a": 900.0}
    assert nutrients.vitamin_a_preformed == 450.0
    assert nutrients.to_json() == {"vitamin_a": 900.0, "vitamin_a_preformed": 450.0}
    assert SupplementNutrients.from_json(None).to_json() == {}

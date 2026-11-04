"""Label amounts convert only by fixed rules; everything else stays a label row."""

import pytest

from src.domain.model.supplement import (
    LabelRow,
    canonical_amount,
    dose,
    nutrients_from_label,
    preformed_share,
)


@pytest.mark.parametrize(
    ("nutrient", "amount", "unit", "form", "expected"),
    [
        ("vitamin_d", 1000, "IU", "Vitamin D3 (cholecalciferol)", 25.0),
        ("vitamin_d", 25, "µg", "Colecalciferol", 25.0),
        ("vitamin_c", 1, "g", "Ascorbic acid", 1000.0),
        ("vitamin_b12", 0.5, "mg", "Cyanocobalamin", 500.0),
        ("vitamin_a", 1000, "IU", "Vitamin A (as retinyl palmitate)", 300.0),
        ("vitamin_a", 1000, "IU", "Vitamin A (as beta-carotene)", 300.0),
        ("vitamin_a", 900, "mcg RAE", "Vitamin A", 900.0),
        ("vitamin_e", 100, "IU", "Vitamin E (as d-alpha tocopherol)", 67.0),
        ("vitamin_e", 100, "IU", "Vitamin E (as dl-alpha tocopheryl acetate)", 45.0),
        ("vitamin_e", 10, "mg α-TE", "Vitamin E", 10.0),
        ("folate", 400, "mcg", "Folic acid", 680.0),
        ("folate", 680, "mcg DFE", "Folate", 680.0),
        ("folate", 400, "mcg", "L-5-methyltetrahydrofolate", 680.0),
    ],
)
def test_fixed_conversions(nutrient, amount, unit, form, expected):
    assert canonical_amount(nutrient, amount, unit, form) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("nutrient", "amount", "unit", "form"),
    [
        ("vitamin_e", 100, "IU", "Vitamin E"),  # form not stated
        ("folate", 400, "mcg", "Folate"),  # food folate, no DFE basis
        ("vitamin_c", 500, None, "Vitamin C"),
        ("vitamin_c", 500, "capsules", "Vitamin C"),
        ("iodine", 150, "mcg", "Potassium iodide"),
        ("vitamin_c", -5, "mg", "Vitamin C"),
    ],
)
def test_uncertain_rows_stay_untracked(nutrient, amount, unit, form):
    assert canonical_amount(nutrient, amount, unit, form) is None


def test_preformed_share_by_form():
    assert preformed_share("Vitamin A (as retinyl palmitate)") == 1.0
    assert preformed_share("Beta-carotene") == 0.0
    assert preformed_share("Vitamin A (retinyl acetate, beta-carotene)") is None
    assert preformed_share("Vitamin A") is None
    # A percentage split names both forms, whichever word the label prints.
    assert preformed_share("Vitamin A (40% as beta-carotene)") is None
    assert preformed_share("Vitamin A (100% as retinyl palmitate)") == 1.0


def test_label_to_per_unit_nutrients_for_a_two_capsule_serving():
    rows = [
        LabelRow("Vitamin A (as retinyl palmitate)", 1000, "IU", "vitamin_a"),
        LabelRow("Vitamin D3", 50, "mcg", "vitamin_d"),
        LabelRow("Vitamin C (ascorbic acid)", 250, "mg", "vitamin_c"),
        LabelRow("Calcium ascorbate", 250, "mg", "vitamin_c"),
        LabelRow("Iodine", 150, "mcg"),  # not tracked
        LabelRow("Vitamin E", 30, "IU", "vitamin_e"),  # form unknown
        LabelRow("Fish oil", 1000, "mg"),
    ]

    nutrients = nutrients_from_label(rows, units_per_serving=2)

    assert nutrients.amounts == {
        "vitamin_a": 150.0,
        "vitamin_d": 25.0,
        "vitamin_c": 250.0,
    }
    assert nutrients.vitamin_a_preformed == 150.0


def test_beta_carotene_does_not_fill_preformed_vitamin_a():
    nutrients = nutrients_from_label(
        [LabelRow("Beta-carotene", 3000, "IU", "vitamin_a")], units_per_serving=1
    )

    assert nutrients.amounts["vitamin_a"] == 900.0
    assert nutrients.vitamin_a_preformed == 0.0


def test_unknown_vitamin_a_form_leaves_preformed_unknown():
    nutrients = nutrients_from_label(
        [LabelRow("Vitamin A", 900, "mcg", "vitamin_a")], units_per_serving=1
    )

    assert nutrients.vitamin_a_preformed is None


def test_per_unit_amounts_keep_precision_so_a_full_serving_logs_the_label_amount():
    per_unit = nutrients_from_label(
        [LabelRow("Selenium", 55, "mcg", "selenium")], units_per_serving=3
    )

    assert dose(per_unit, 3).amounts["selenium"] == 55.0


def test_a_label_with_no_tracked_rows_has_no_nutrients():
    nutrients = nutrients_from_label(
        [LabelRow("Creatine monohydrate", 5, "g")], units_per_serving=1
    )

    assert nutrients.amounts == {}
    assert nutrients.to_micros().vitamin_c is None


def test_serving_size_must_be_positive():
    with pytest.raises(ValueError):
        nutrients_from_label([], units_per_serving=0)

"""Recorded model reads of supplement labels: product, nutrients and food routing."""

import pytest
from pydantic import ValidationError

from src.domain.model.ai.supplement_label_contract import SupplementLabelResponse
from src.domain.model.supplement import ReviewStatus, SupplementForm
from src.domain.model.supplement.label_scan import (
    is_food,
    product_from_label,
)

IMAGE = "https://img.nutree.ai/mealtrack/8c1f2a9e-5b7d-4c3e-9f10-2a6b8d4e1c7a/public"


def _read(**fields) -> SupplementLabelResponse:
    return SupplementLabelResponse.model_validate(
        {"is_supplement_label": True, "product_name": "Label", **fields}
    )


def _product(read: SupplementLabelResponse):
    return product_from_label(
        read, product_id="p1", owner_user_id="u1", label_image_url=IMAGE
    )


MULTIVITAMIN = {
    "brand": "Centrum",
    "product_name": "Centrum Adults",
    "form": "tablet",
    "serving_units": 1,
    "rows": [
        {
            "name": "Vitamin A (as retinyl acetate and 29% as beta-carotene)",
            "amount": 900,
            "unit": "mcg RAE",
            "nutrient": "vitamin_a",
        },
        {
            "name": "Vitamin C (as ascorbic acid)",
            "amount": 90,
            "unit": "mg",
            "nutrient": "vitamin_c",
        },
        {
            "name": "Vitamin D3 (as cholecalciferol)",
            "amount": 25,
            "unit": "mcg",
            "nutrient": "vitamin_d",
        },
        {"name": "Folate", "amount": 400, "unit": "mcg DFE", "nutrient": "folate"},
        {
            "name": "Zinc (as zinc oxide)",
            "amount": 11,
            "unit": "mg",
            "nutrient": "zinc",
        },
        {
            "name": "Iodine (as potassium iodide)",
            "amount": 150,
            "unit": "mcg",
            "nutrient": None,
        },
    ],
    "barcode": "0300054479907",
}


def test_a_multivitamin_keeps_every_row_and_counts_tracked_ones_per_tablet():
    product = _product(_read(**MULTIVITAMIN))

    assert product.nutrients.amounts == {
        "vitamin_a": 900.0,
        "vitamin_c": 90.0,
        "vitamin_d": 25.0,
        "folate": 400.0,
        "zinc": 11.0,
    }
    # The vitamin A split is given as a percentage, so its form stays unknown.
    assert product.nutrients.vitamin_a_preformed is None
    assert [row.name for row in product.label_rows][-1].startswith("Iodine")
    assert product.review_status is ReviewStatus.PENDING_REVIEW
    assert product.owner_user_id == "u1" and product.label_image_url == IMAGE
    assert product.unit_label == "viên" and product.barcodes == ("00300054479907",)


def test_a_d3_softgel_in_iu_converts_to_micrograms():
    product = _product(
        _read(
            brand="Nature Made",
            product_name="Vitamin D3 1000 IU",
            form="softgel",
            rows=[
                {
                    "name": "Vitamin D3",
                    "amount": 1000,
                    "unit": "IU",
                    "nutrient": "vitamin_d",
                }
            ],
        )
    )

    assert product.nutrients.amounts == {"vitamin_d": 25.0}
    assert product.form is SupplementForm.SOFTGEL
    assert product.unit_label == "viên nang mềm"


def test_retinyl_palmitate_is_all_preformed_vitamin_a():
    product = _product(
        _read(
            brand="Now",
            rows=[
                {
                    "name": "Vitamin A (as retinyl palmitate)",
                    "amount": 3000,
                    "unit": "mcg",
                    "nutrient": "vitamin_a",
                }
            ],
        )
    )

    assert product.nutrients.vitamin_a_preformed == 3000.0


def test_a_zinc_tablet_and_a_vietnamese_label_read_the_same_way():
    zinc = _product(
        _read(
            brand="Kirkland",
            rows=[{"name": "Zinc", "amount": 50, "unit": "mg", "nutrient": "zinc"}],
        )
    )
    vietnamese = _product(
        _read(
            brand="Mekophar",
            product_name="Vitamin C 500mg",
            form="tablet",
            rows=[
                {
                    "name": "Vitamin C (Acid ascorbic)",
                    "amount": 500,
                    "unit": "mg",
                    "nutrient": "vitamin_c",
                }
            ],
        )
    )

    assert zinc.nutrients.amounts == {"zinc": 50.0}
    assert vietnamese.nutrients.amounts == {"vitamin_c": 500.0}


@pytest.mark.parametrize(
    ("fields", "food"),
    [
        # 2 softgels at 20 kcal: 10 kcal a unit stays a supplement.
        (
            {
                "form": "softgel",
                "serving_units": 2,
                "macros_per_serving": {"calories_kcal": 20, "fat_g": 2},
            },
            False,
        ),
        # 3 gummies at 30 kcal: 10 kcal a gummy.
        (
            {
                "form": "gummy",
                "serving_units": 3,
                "macros_per_serving": {"calories_kcal": 30, "carbs_g": 7},
            },
            False,
        ),
        (
            {"product_kind": "protein_powder", "form": "powder", "serving_grams": 30},
            True,
        ),
        # Called a supplement, but 24 g protein in one scoop is food.
        (
            {
                "form": "powder",
                "serving_units": 1,
                "macros_per_serving": {"calories_kcal": 120, "protein_g": 24},
            },
            True,
        ),
    ],
)
def test_food_routing_by_kind_or_by_amount_per_unit(fields, food):
    assert is_food(_read(**fields)) is food


def test_a_product_without_brand_or_tracked_nutrients_stays_private():
    no_brand = _product(
        _read(
            rows=[
                {
                    "name": "Vitamin C",
                    "amount": 500,
                    "unit": "mg",
                    "nutrient": "vitamin_c",
                }
            ]
        )
    )
    herbs_only = _product(
        _read(
            brand="Blackmores",
            rows=[{"name": "Milk thistle", "amount": 7, "unit": "g"}],
        )
    )

    assert no_brand.review_status is ReviewStatus.PRIVATE
    assert herbs_only.review_status is ReviewStatus.PRIVATE
    assert herbs_only.nutrients.amounts == {}


def test_the_contract_refuses_unknown_nutrient_keys_and_forms():
    with pytest.raises(ValidationError):
        _read(
            rows=[
                {"name": "Iodine", "amount": 150, "unit": "mcg", "nutrient": "iodine"}
            ]
        )
    with pytest.raises(ValidationError):
        _read(form="lozenge")


def test_odd_model_values_are_set_aside_instead_of_failing_the_read():
    read = SupplementLabelResponse.model_validate(
        {
            "is_supplement_label": True,
            "product_name": "",
            "form": "liquid",
            "serving_units": 237,
            "serving_grams": 0,
            "rows": [
                {"name": "", "amount": 5, "unit": "mg"},
                {
                    "name": "Folate",
                    "amount": -1,
                    "unit": "mcg DFE (400 mcg folic acid)",
                },
            ],
        }
    )

    assert read.product_name == "Thực phẩm bổ sung"
    assert read.serving_units == 237 and read.serving_grams is None
    [row] = read.rows
    assert row.amount is None and row.unit.startswith("mcg DFE")


@pytest.mark.parametrize(
    ("fields", "food"),
    [
        # A 237 ml shake at 220 kcal is food even though each ml is under 1 kcal.
        (
            {
                "form": "liquid",
                "serving_units": 237,
                "macros_per_serving": {"calories_kcal": 220, "protein_g": 9},
            },
            True,
        ),
        # A cod liver oil spoon stays a supplement.
        (
            {
                "form": "liquid",
                "serving_units": 5,
                "macros_per_serving": {"calories_kcal": 40, "fat_g": 4.5},
            },
            False,
        ),
    ],
)
def test_served_forms_are_judged_per_serving(fields, food):
    assert is_food(_read(**fields)) is food

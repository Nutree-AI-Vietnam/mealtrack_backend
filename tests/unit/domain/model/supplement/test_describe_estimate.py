"""A supplement guessed from a Ghi nhanh sentence: private, estimated, sane amounts."""

import pytest

from src.domain.model.supplement import ProductSource, ReviewStatus
from src.domain.model.supplement.describe_estimate import (
    estimate_row,
    estimated_product,
)


def _product(*rows, form="softgel"):
    return estimated_product(
        product_id="p-1", owner_user_id="u-1", name="D3", form=form, rows=rows
    )


def test_a_guess_is_the_persons_private_estimate():
    product = _product(estimate_row("vitamin_d", 1000, "IU"))

    assert product.estimated is True
    assert product.source is ProductSource.DESCRIBE
    assert product.review_status is ReviewStatus.PRIVATE
    assert product.owner_user_id == "u-1" and product.visible_to("u-2") is False
    assert product.unit_label == "viên nang mềm" and product.units_per_serving == 1
    assert product.nutrients.amounts == {"vitamin_d": 25.0}


def test_folate_is_read_as_folic_acid_in_dfe():
    product = _product(estimate_row("folate", 400, "mcg"), form="tablet")

    assert product.nutrients.amounts == {"folate": pytest.approx(680.0)}
    assert product.label_rows[0].name == "Folic acid"


def test_a_unit_slip_is_dropped_not_counted():
    product = _product(
        estimate_row("vitamin_d", 25000, "mcg"),  # 250 times the limit
        estimate_row("vitamin_c", 1000, "mg"),
        estimate_row("vitamin_b12", 5000, "mcg"),  # no upper limit, real dose
        estimate_row("vitamin_k", 200, "mcg"),
    )

    assert product.nutrients.amounts == {
        "vitamin_c": 1000.0,
        "vitamin_b12": 5000.0,
        "vitamin_k": 200.0,
    }


@pytest.mark.parametrize(
    "row",
    [
        estimate_row("vitamin_b12", 1000, "mg"),  # mcg read as mg
        estimate_row("potassium", 99, "g"),
        estimate_row("thiamin", 50, "g"),
    ],
)
def test_a_slip_on_a_nutrient_without_a_limit_is_dropped_too(row):
    assert _product(row).nutrients.amounts == {}


def test_preformed_vitamin_a_goes_with_its_total():
    kept = _product(estimate_row("vitamin_a", 1000, "mcg"))
    dropped = _product(estimate_row("vitamin_a", 900000, "mcg"))

    assert kept.nutrients.amounts == {"vitamin_a": 1000.0}
    assert dropped.nutrients.amounts == {}
    assert dropped.nutrients.vitamin_a_preformed is None


def test_a_guess_with_no_tracked_nutrient_still_logs():
    product = _product(form="tablet")

    assert product.nutrients.amounts == {} and product.label_rows == ()

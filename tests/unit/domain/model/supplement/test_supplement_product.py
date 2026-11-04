"""Product visibility, invariants and barcodes; intakes snapshot their dose."""

from datetime import UTC, date, datetime

import pytest

from src.domain.model.supplement import (
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementIntake,
    SupplementNutrients,
    SupplementProduct,
    UserSupplement,
    gtin14,
)

OWNER, OTHER = "user-1", "user-2"


def _product(**overrides) -> SupplementProduct:
    fields = {
        "id": "p-1",
        "name": "Vitamin D3 1000 IU",
        "form": SupplementForm.SOFTGEL,
        "unit_label": "viên nang mềm",
        "source": ProductSource.CURATED,
        "review_status": ReviewStatus.APPROVED,
        "nutrients": SupplementNutrients({"vitamin_d": 25.0}),
    }
    return SupplementProduct(**{**fields, **overrides})


def test_shared_products_are_visible_only_once_approved():
    assert _product().visible_to(OTHER)
    pending = _product(review_status=ReviewStatus.PENDING_REVIEW)
    assert not pending.visible_to(OWNER)


def test_own_products_are_visible_only_to_their_owner():
    own = _product(
        owner_user_id=OWNER,
        source=ProductSource.LABEL_SCAN,
        review_status=ReviewStatus.PENDING_REVIEW,
    )

    assert own.visible_to(OWNER)
    assert not own.visible_to(OTHER)


@pytest.mark.parametrize(
    "overrides",
    [
        {"review_status": ReviewStatus.PRIVATE},  # shared yet private
        {"owner_user_id": OWNER},  # approved yet owned
        {"estimated": True},  # estimate from a curated row
        {"source": ProductSource.DESCRIBE, "estimated": True},  # shared estimate
        {"units_per_serving": 0},
        {"name": "  "},
    ],
)
def test_invalid_products_are_rejected(overrides):
    with pytest.raises(ValueError):
        _product(**overrides)


def test_an_estimate_from_a_sentence_stays_private():
    estimate = _product(
        owner_user_id=OWNER,
        source=ProductSource.DESCRIBE,
        review_status=ReviewStatus.PRIVATE,
        estimated=True,
    )

    assert estimate.visible_to(OWNER) and not estimate.visible_to(OTHER)


def test_barcodes_are_stored_as_gtin14_without_duplicates():
    product = _product(barcodes=("012345678905", "0012345678905", "bad", "96385074"))

    assert product.barcodes == ("00012345678905", "00000096385074")
    assert gtin14("9 300807 362341") == "09300807362341"
    assert gtin14("12345") is None
    assert gtin14("9300807362342") is None  # wrong check digit


def test_an_intake_snapshots_the_dose_and_the_estimate_flag():
    product = _product(
        nutrients=SupplementNutrients(
            {"vitamin_a": 300.0, "zinc": 5.0}, vitamin_a_preformed=300.0
        )
    )

    intake = SupplementIntake.log(
        id="i-1",
        user_id=OWNER,
        product=product,
        units=2,
        taken_at=datetime(2026, 11, 1, 1, 0, tzinfo=UTC),
        local_date=date(2026, 11, 1),
        logged_on_day=True,
    )

    assert intake.product_id == "p-1"
    assert intake.nutrients.amounts == {"vitamin_a": 600.0, "zinc": 10.0}
    assert intake.nutrients.vitamin_a_preformed == 600.0
    assert intake.estimated is False


def test_intakes_and_list_rows_need_positive_units_and_aware_times():
    with pytest.raises(ValueError):
        UserSupplement(id="l-1", user_id=OWNER, product_id="p-1", units_per_dose=0)
    with pytest.raises(ValueError):
        SupplementIntake(
            id="i-1",
            user_id=OWNER,
            product_id="p-1",
            units=1,
            taken_at=datetime(2026, 11, 1, 8, 0),
            local_date=date(2026, 11, 1),
            logged_on_day=True,
            nutrients=SupplementNutrients(),
        )

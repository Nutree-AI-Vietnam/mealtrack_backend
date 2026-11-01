"""Curated catalog records shaped into products, and records the import refuses."""

from __future__ import annotations

import pytest

from src.domain.model.supplement import ProductSource, ReviewStatus, SupplementForm
from src.domain.model.supplement.catalog_entry import (
    CatalogRecordError,
    catalog_entry,
)


def _record(**overrides) -> dict:
    record = {
        "key": "au:blackmores-bio-c-1000",
        "brand": "Blackmores",
        "name": "Bio C 1000",
        "name_original": None,
        "form": "tablet",
        "unit_label": "viên",
        "units_per_serving": 1.0,
        "barcodes": ["9300807362341"],
        "origin_country": "AU",
        "is_medicine_in_vn": False,
        "sold_in_vn": True,
        "source_name": "manufacturer",
        "source_url": "https://www.blackmores.com.au/products/bio-c-1000mg",
        "label_rows": [
            {
                "name": "Ascorbic acid",
                "name_original": None,
                "amount": 1000,
                "unit": "mg",
                "equivalent": None,
            }
        ],
        "nutrients": {"vitamin_c": 1000.0},
        "vitamin_a_preformed": None,
    }
    return {**record, **overrides}


def test_a_clean_record_is_an_approved_shared_curated_product():
    entry = catalog_entry(_record())

    product = entry.product
    assert entry.reasons == ()
    assert product.shared
    assert product.source is ProductSource.CURATED
    assert product.review_status is ReviewStatus.APPROVED
    assert product.catalog_key == "au:blackmores-bio-c-1000"
    assert product.form is SupplementForm.TABLET
    assert product.barcodes == ("09300807362341",)
    assert product.nutrients.amounts == {"vitamin_c": 1000.0}
    assert product.label_rows[0].name == "Ascorbic acid"
    assert product.label_rows[0].amount == 1000.0


def test_bad_barcodes_are_left_off_and_listed():
    entry = catalog_entry(_record(barcodes=["9300807362341", "9300807362342"]))

    assert entry.product.barcodes == ("09300807362341",)
    assert entry.invalid_barcodes == ("9300807362342",)


def test_a_record_marked_as_food_stays_macro_bearing():
    assert catalog_entry(_record(macro_bearing=True)).product.macro_bearing
    assert not catalog_entry(_record()).product.macro_bearing


def test_a_record_with_issues_waits_for_review_with_its_reasons():
    entry = catalog_entry(_record(issues=["unknown serving unit 'Oz(s)'"]))

    assert entry.reasons == ("unknown serving unit 'Oz(s)'",)
    assert entry.product.review_status is ReviewStatus.PENDING_REVIEW
    assert entry.product.shared


def test_a_printed_equivalent_is_folded_into_the_row_name():
    row = {
        "name": "Calcium carbonate",
        "name_original": None,
        "amount": 1500,
        "unit": "mg",
        "equivalent": {"name": "calcium", "amount": 600.0, "unit": "mg"},
    }

    product = catalog_entry(_record(label_rows=[row])).product

    assert product.label_rows[0].name == "Calcium carbonate (= calcium 600 mg)"
    assert product.label_rows[0].amount == 1500.0


def test_a_vietnamese_label_keeps_its_printed_row_names():
    row = {
        "name": "Ferrous fumarate",
        "name_original": "Sắt (II) fumarat",
        "amount": 200,
        "unit": "mg",
        "equivalent": None,
    }

    product = catalog_entry(_record(origin_country="VN", label_rows=[row])).product

    assert product.label_rows[0].name == "Sắt (II) fumarat"


def test_zero_amounts_are_dropped_from_nutrients_and_rows():
    rows = [
        {"name": "Sodium", "amount": 0, "unit": "mg"},
        {"name": "Vitamin C", "amount": 500, "unit": "mg"},
    ]

    product = catalog_entry(
        _record(label_rows=rows, nutrients={"sodium": 0, "vitamin_c": 500.0})
    ).product

    assert product.nutrients.amounts == {"vitamin_c": 500.0}
    assert [row.name for row in product.label_rows] == ["Vitamin C"]


def test_a_printed_range_stays_as_text_on_its_row():
    rows = [{"name": "Fat", "amount": "0~0.031", "unit": "g"}]

    row = catalog_entry(_record(label_rows=rows)).product.label_rows[0]

    assert row.name == "Fat 0~0.031 g"
    assert row.amount is None
    assert row.unit is None


def test_preformed_vitamin_a_is_dropped_without_a_vitamin_a_amount():
    product = catalog_entry(
        _record(nutrients={"vitamin_a": 0, "zinc": 8.0}, vitamin_a_preformed=0.0)
    ).product

    assert product.nutrients.vitamin_a_preformed is None
    assert product.nutrients.amounts == {"zinc": 8.0}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"unit_label": None}, "missing unit_label"),
        ({"unit_label": "  "}, "missing unit_label"),
        ({"name": None}, "missing name"),
        ({"key": None}, "missing key"),
        ({"form": "Other (e.g. tea bag) [E0172]"}, "unknown form"),
        ({"nutrients": {"omega_3": 300.0}}, "omega_3 is not a tracked"),
        ({"nutrients": {"zinc": -1}}, "zinc must be"),
        ({"units_per_serving": 0}, "units_per_serving must be more than zero"),
        ({"units_per_serving": 10**400}, "units_per_serving is too large"),
        ({"nutrients": {"zinc": 10**400}}, "zinc is too large"),
        ({"sold_in_vn": "yes"}, "sold_in_vn must be true or false"),
        ({"barcodes": "9300807362341"}, "barcodes must be a list"),
        ({"key": "x" * 129}, "key is longer than 128"),
        ({"label_rows": [{"name": "", "amount": 1}]}, "needs a name"),
    ],
)
def test_invalid_records_are_refused_with_a_reason(overrides, message):
    with pytest.raises(CatalogRecordError, match=message):
        catalog_entry(_record(**overrides))

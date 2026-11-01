"""Catalog import: invalid and repeated records are reported, the rest written."""

from __future__ import annotations

import pytest

from src.app.services.supplement_catalog_import import import_catalog, read_catalog
from src.domain.ports.supplement_catalog_port import BarcodeSkip, CatalogWriteCounts


def _record(key: str, **overrides) -> dict:
    return {
        "key": key,
        "brand": "Blackmores",
        "name": f"Product {key}",
        "form": "capsule",
        "unit_label": "viên nang",
        "units_per_serving": 1,
        "nutrients": {"zinc": 8.0},
        "label_rows": [],
        "barcodes": [],
        **overrides,
    }


def test_read_catalog_keeps_file_order_and_reports_bad_records():
    entries, invalid = read_catalog(
        [
            _record("a"),
            "not a record",
            _record("b", unit_label=None),
            _record("a", name="Again"),
            _record("c", issues=["collector note: unsure"]),
        ]
    )

    assert [e.product.catalog_key for e in entries] == ["a", "c"]
    assert [(i.record, i.error) for i in invalid] == [
        ("#1", "a record must be an object"),
        ("b", "missing unit_label"),
        ("a", "the key appears more than once"),
    ]


@pytest.mark.asyncio
async def test_import_reports_counts_from_the_catalog_writes():
    class _Catalog:
        async def upsert_curated(self, entries):
            self.keys = [e.product.catalog_key for e in entries]
            return CatalogWriteCounts(
                inserted=1,
                unchanged=1,
                barcode_skips=[BarcodeSkip("c", "00012345678905", "on a")],
            )

    catalog = _Catalog()
    summary = await import_catalog(
        catalog,
        [
            _record("a", barcodes=["9300807362342"]),
            _record("c", issues=["unit unclear"]),
            _record("x", form="pill"),
        ],
    )

    assert catalog.keys == ["a", "c"]
    assert summary.received == 3
    assert summary.held_back == 1
    assert (summary.inserted, summary.unchanged) == (1, 1)
    assert summary.invalid[0].record == "x"
    assert [(s.catalog_key, s.barcode, s.reason) for s in summary.barcode_skips] == [
        ("a", "9300807362342", "not a valid GTIN"),
        ("c", "00012345678905", "on a"),
    ]

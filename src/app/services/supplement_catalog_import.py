"""Curated catalog import: check every record, then write the valid ones by key."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from src.domain.model.supplement.catalog_entry import (
    CatalogEntry,
    CatalogRecordError,
    catalog_entry,
)
from src.domain.ports.supplement_catalog_port import (
    BarcodeSkip,
    SupplementCatalogPort,
)


@dataclass(frozen=True)
class InvalidRecord:
    record: str
    error: str


@dataclass
class SupplementImportSummary:
    received: int
    held_back: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    kept_reviewed: int = 0
    kept_approved: int = 0
    kept_approved_keys: list[str] = field(default_factory=list)
    invalid: list[InvalidRecord] = field(default_factory=list)
    barcode_skips: list[BarcodeSkip] = field(default_factory=list)


def read_catalog(
    records: Sequence[object],
) -> tuple[list[CatalogEntry], list[InvalidRecord]]:
    """Valid entries in file order; a repeated key keeps its first record."""
    entries: list[CatalogEntry] = []
    invalid: list[InvalidRecord] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        label = f"#{index}"
        if not isinstance(record, Mapping):
            invalid.append(InvalidRecord(label, "a record must be an object"))
            continue
        if isinstance(record.get("key"), str):
            label = record["key"]
        try:
            entry = catalog_entry(record)
        except CatalogRecordError as error:
            invalid.append(InvalidRecord(label, str(error)))
            continue
        key = entry.product.catalog_key or ""
        if key in seen:
            invalid.append(InvalidRecord(label, "the key appears more than once"))
            continue
        seen.add(key)
        entries.append(entry)
    return entries, invalid


async def import_catalog(
    catalog: SupplementCatalogPort, records: Sequence[object]
) -> SupplementImportSummary:
    """Write the catalog in the caller's transaction; the caller commits or not."""
    entries, invalid = read_catalog(records)
    counts = await catalog.upsert_curated(entries)
    unreadable = [
        BarcodeSkip(entry.product.catalog_key or "", code, "not a valid GTIN")
        for entry in entries
        for code in entry.invalid_barcodes
    ]
    return SupplementImportSummary(
        received=len(records),
        held_back=sum(1 for entry in entries if entry.reasons),
        inserted=counts.inserted,
        updated=counts.updated,
        unchanged=counts.unchanged,
        kept_reviewed=counts.kept_reviewed,
        kept_approved=counts.kept_approved,
        kept_approved_keys=counts.kept_approved_keys,
        invalid=invalid,
        barcode_skips=unreadable + counts.barcode_skips,
    )

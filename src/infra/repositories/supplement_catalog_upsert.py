"""Curated catalog rows written by catalog key, in one pass over the file."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from sqlalchemy import delete, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.supplement import ReviewStatus
from src.domain.model.supplement.catalog_entry import CatalogEntry
from src.domain.ports.supplement_catalog_port import BarcodeSkip, CatalogWriteCounts
from src.infra.database.models.supplement import (
    SupplementProductBarcodeORM as Barcode,
)
from src.infra.database.models.supplement import (
    SupplementProductORM as Product,
)
from src.infra.mappers.supplement_mapper import product_values

NO_SYNC = {"synchronize_session": False}
_CHUNK = 1000


async def upsert_curated_rows(
    session: AsyncSession, entries: Sequence[CatalogEntry]
) -> CatalogWriteCounts:
    """Insert new keys, update changed ones, and count the rest as unchanged.

    Rows an admin reviewed are skipped; the file no longer owns them.
    """
    counts = CatalogWriteCounts()
    existing = await _curated_rows(session, [e.product.catalog_key for e in entries])
    claims, held = await _shared_barcodes(session)
    # A code an updated entry no longer lists is free for a later entry.
    for entry in entries:
        row = existing.get(entry.product.catalog_key or "")
        if row is not None and _file_owns(row, entry):
            for code in held.get(row.id, set()) - set(entry.product.barcodes):
                claims.pop(code, None)

    inserts: list[dict[str, Any]] = []
    updates: list[tuple[str, dict[str, Any]]] = []
    new_codes: list[dict[str, Any]] = []
    gone_codes: list[tuple[str, str]] = []
    for entry in entries:
        key = entry.product.catalog_key or ""
        row = existing.get(key)
        if row is not None and row.reviewed_at is not None:
            counts.kept_reviewed += 1
            continue
        if row is not None and not _file_owns(row, entry):
            counts.kept_approved += 1
            counts.kept_approved_keys.append(key)
            continue
        product_id = row.id if row is not None else entry.product.id
        values = product_values(replace(entry.product, id=product_id))
        values["review_reasons"] = list(entry.reasons) or None
        kept = []
        for code in entry.product.barcodes:
            holder_id, holder_key = claims.setdefault(code, (product_id, key))
            if holder_id == product_id:
                kept.append(code)
            else:
                counts.barcode_skips.append(
                    BarcodeSkip(key, code, f"on {holder_key or holder_id}")
                )
        have = held.get(product_id, set())
        added = [code for code in kept if code not in have]
        removed = have - set(kept)
        new_codes += [
            {"product_id": product_id, "barcode": code, "owner_user_id": None}
            for code in added
        ]
        gone_codes += [(product_id, code) for code in removed]
        if row is None:
            inserts.append(values)
            counts.inserted += 1
            continue
        changed = {
            column: value
            for column, value in values.items()
            if column != "id" and getattr(row, column) != value
        }
        if changed:
            updates.append((product_id, changed))
        if changed or added or removed:
            counts.updated += 1
        else:
            counts.unchanged += 1

    # Free codes before others take them; products before their barcodes.
    for pairs in _chunks(gone_codes):
        await session.execute(
            delete(Barcode)
            .where(tuple_(Barcode.product_id, Barcode.barcode).in_(pairs))
            .execution_options(**NO_SYNC)
        )
    for chunk in _chunks(inserts):
        await session.execute(insert(Product), chunk)
    for product_id, changed in updates:
        await session.execute(
            update(Product)
            # An admin's review that landed meanwhile wins over the file.
            .where(Product.id == product_id, Product.reviewed_at.is_(None))
            .values(**changed)
            .execution_options(**NO_SYNC)
        )
    for codes in _chunks(new_codes):
        await session.execute(pg_insert(Barcode).on_conflict_do_nothing(), codes)
    return counts


def _file_owns(row: Any, entry: CatalogEntry) -> bool:
    """Whether the file may rewrite this row.

    Not once an admin reviewed it, and never to take an approved row back out:
    people may already list it and log doses of it.
    """
    if row.reviewed_at is not None:
        return False
    return not (
        row.review_status == ReviewStatus.APPROVED.value
        and entry.product.review_status is not ReviewStatus.APPROVED
    )


async def _curated_rows(
    session: AsyncSession, keys: Iterable[str | None]
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for chunk in _chunks([key for key in keys if key]):
        found = await session.execute(
            select(Product).where(Product.catalog_key.in_(chunk))
        )
        rows.update({str(row.catalog_key): row for row in found.scalars().all()})
    return rows


async def _shared_barcodes(
    session: AsyncSession,
) -> tuple[dict[str, tuple[str, str | None]], dict[str, set[str]]]:
    """Each shared barcode's product (id, catalog key), and each product's codes."""
    rows = await session.execute(
        select(Barcode.barcode, Barcode.product_id, Product.catalog_key)
        .join(Product, Product.id == Barcode.product_id)
        .where(Barcode.owner_user_id.is_(None))
    )
    claims: dict[str, tuple[str, str | None]] = {}
    held: dict[str, set[str]] = {}
    for code, product_id, key in rows.all():
        claims[code] = (product_id, key)
        held.setdefault(product_id, set()).add(code)
    return claims, held


def _chunks[T](items: Sequence[T]) -> Iterable[list[T]]:
    for start in range(0, len(items), _CHUNK):
        yield list(items[start : start + _CHUNK])

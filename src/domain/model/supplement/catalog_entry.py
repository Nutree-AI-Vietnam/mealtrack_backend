"""Curated catalog records, checked and shaped into products before import."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from src.domain.model.supplement.dose_math import SupplementNutrients
from src.domain.model.supplement.supplement_product import (
    LabelRow,
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementProduct,
    gtin14,
)

# Column widths in supplement_products, so a long value fails one record, not the batch.
_MAX_LENGTH = {
    "key": 128,
    "brand": 255,
    "name": 255,
    "name_original": 255,
    "unit_label": 32,
    "origin_country": 8,
    "source_name": 255,
    "source_url": 1024,
}


class CatalogRecordError(ValueError):
    """A catalog record that cannot become a product; the message says why."""


@dataclass(frozen=True)
class CatalogEntry:
    """A shared curated product; ``reasons`` holds it back for review.

    ``invalid_barcodes`` lists codes that fail the GTIN check and were left off.
    """

    product: SupplementProduct
    reasons: tuple[str, ...] = ()
    invalid_barcodes: tuple[str, ...] = ()


def catalog_entry(record: Mapping[str, object]) -> CatalogEntry:
    """One record of ``build_catalog.py``'s output as a shared curated product.

    Records carrying ``issues`` land in ``pending_review`` with those reasons;
    the rest are approved. Zero amounts are dropped, so "Sodium 0 mg" does not
    make the product look like a source of sodium.
    """
    origin = _text(record, "origin_country")
    reasons = _reasons(record.get("issues"))
    barcodes = _barcodes(record.get("barcodes"))
    try:
        product = SupplementProduct(
            id=str(uuid.uuid4()),
            name=_required_text(record, "name"),
            form=_form(record.get("form")),
            unit_label=_required_text(record, "unit_label"),
            source=ProductSource.CURATED,
            review_status=ReviewStatus.PENDING_REVIEW
            if reasons
            else ReviewStatus.APPROVED,
            brand=_text(record, "brand"),
            name_original=_text(record, "name_original"),
            units_per_serving=_amount(
                record.get("units_per_serving"), "units_per_serving"
            ),
            nutrients=nutrients_without_zeros(
                record.get("nutrients"), record.get("vitamin_a_preformed")
            ),
            label_rows=_label_rows(record.get("label_rows"), origin),
            barcodes=barcodes,
            catalog_key=_required_text(record, "key"),
            origin_country=origin,
            is_medicine_in_vn=_flag(record, "is_medicine_in_vn", required=True),
            sold_in_vn=_flag(record, "sold_in_vn", required=True),
            source_name=_text(record, "source_name"),
            source_url=_text(record, "source_url"),
            macro_bearing=_flag(record, "macro_bearing"),
        )
    except CatalogRecordError:
        raise
    except ValueError as error:
        raise CatalogRecordError(str(error)) from None
    return CatalogEntry(
        product=product,
        reasons=reasons,
        invalid_barcodes=tuple(code for code in barcodes if gtin14(code) is None),
    )


def nutrients_without_zeros(
    raw: object, vitamin_a_preformed: object = None
) -> SupplementNutrients:
    """Per-unit amounts with zeros dropped; unknown keys are an error."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise CatalogRecordError("nutrients must be an object")
    amounts = {str(key): value for key, value in raw.items() if value != 0}
    # Preformed vitamin A only means something next to a vitamin A amount.
    preformed = vitamin_a_preformed if amounts.get("vitamin_a") else None
    try:
        return SupplementNutrients(amounts=amounts, vitamin_a_preformed=preformed)  # type: ignore[arg-type]
    except ValueError as error:
        raise CatalogRecordError(str(error)) from None


def _label_rows(raw: object, origin: str | None) -> tuple[LabelRow, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CatalogRecordError("label_rows must be a list")
    rows = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise CatalogRecordError("a label row must be an object")
        name = _row_name(item, origin)
        amount = item.get("amount")
        unit = None if item.get("unit") is None else str(item["unit"]).strip() or None
        if isinstance(amount, str) and amount.strip():
            # A printed range ("0~0.031 g") stays as text; it never counts.
            name = " ".join(part for part in (name, amount.strip(), unit) if part)
            amount = unit = None
        elif amount is not None:
            amount = _amount(amount, f"the amount of {name!r}", allow_zero=True)
            if amount == 0:
                continue
        rows.append(LabelRow(name=name, amount=amount, unit=unit))
    return tuple(rows)


def _row_name(item: Mapping[str, object], origin: str | None) -> str:
    # A Vietnamese label reads best as printed; other labels in English.
    printed = item.get("name_original") if origin == "VN" else None
    name = str(printed or item.get("name") or "").strip()
    if not name:
        raise CatalogRecordError("a label row needs a name")
    equivalent = item.get("equivalent")
    if isinstance(equivalent, Mapping) and equivalent.get("name"):
        amount = equivalent.get("amount")
        parts = (
            str(equivalent["name"]).strip(),
            f"{_amount(amount, f'the equivalent of {name!r}', allow_zero=True):g}"
            if isinstance(amount, int | float) and not isinstance(amount, bool)
            else "",
            str(equivalent.get("unit") or "").strip(),
        )
        # "Calcium carbonate (= calcium 600 mg)" keeps the printed equivalent.
        name = f"{name} (= {' '.join(part for part in parts if part)})"
    return name


def _reasons(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
        raise CatalogRecordError("issues must be a list of text")
    return tuple(reason.strip() for reason in raw if reason.strip())


def _barcodes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(code, str) for code in raw):
        raise CatalogRecordError("barcodes must be a list of text")
    return tuple(raw)


def _form(raw: object) -> SupplementForm:
    try:
        return SupplementForm(str(raw))
    except ValueError:
        raise CatalogRecordError(f"unknown form {raw!r}") from None


def _flag(record: Mapping[str, object], field: str, *, required: bool = False) -> bool:
    value = record.get(field)
    if value is None:
        # A missing Vietnam flag would quietly rank the product as foreign.
        if required:
            raise CatalogRecordError(f"missing {field}")
        return False
    if not isinstance(value, bool):
        raise CatalogRecordError(f"{field} must be true or false")
    return value


def _amount(raw: object, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CatalogRecordError(f"{label} must be a number")
    try:
        raw = float(raw)
    except OverflowError:
        raise CatalogRecordError(f"{label} is too large") from None
    if not math.isfinite(raw) or raw < 0 or (raw == 0 and not allow_zero):
        raise CatalogRecordError(f"{label} must be more than zero")
    return float(raw)


def _required_text(record: Mapping[str, object], field: str) -> str:
    value = _text(record, field)
    if value is None:
        raise CatalogRecordError(f"missing {field}")
    return value


def _text(record: Mapping[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CatalogRecordError(f"{field} must be text")
    value = value.strip()
    if len(value) > _MAX_LENGTH[field]:
        raise CatalogRecordError(
            f"{field} is longer than {_MAX_LENGTH[field]} characters"
        )
    return value or None

"""What an admin may change on a product waiting for review, and what never enters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from src.domain.model.supplement.catalog_entry import nutrients_without_zeros
from src.domain.model.supplement.dose_math import PREFORMED_VITAMIN_A_KEY
from src.domain.model.supplement.supplement_product import (
    LabelRow,
    ProductSource,
    ReviewStatus,
    SupplementForm,
    SupplementProduct,
)

EDITABLE_FIELDS = (
    "brand",
    "name",
    "form",
    "unit_label",
    "units_per_serving",
    "nutrients",
    "label_rows",
    "sold_in_vn",
    "is_medicine_in_vn",
    "macro_bearing",
)


class SupplementNotReviewableError(ValueError):
    """The product is not waiting for review, or can never be shared."""


class SupplementReviewConflictError(SupplementNotReviewableError):
    """Another write to the same barcode or list row landed first; retry."""


def require_reviewable(product: SupplementProduct, *, editing: bool = False) -> None:
    """A row waiting for review takes any action; a live shared row only edits.

    A live row is never withdrawn: people may list it and log doses of it.
    """
    # A Ghi nhanh estimate is a guess from a sentence, never a catalog entry.
    if product.source is ProductSource.DESCRIBE or product.estimated:
        raise SupplementNotReviewableError("a Ghi nhanh estimate is never shared")
    if product.review_status is ReviewStatus.PENDING_REVIEW:
        return
    if product.review_status is ReviewStatus.APPROVED and product.shared:
        if editing:
            return
        raise SupplementNotReviewableError(
            "the product is already in the catalog; it can only be edited"
        )
    raise SupplementNotReviewableError(
        f"the product is {product.review_status.value}, not waiting for review"
    )


def with_edits(
    product: SupplementProduct, changes: Mapping[str, Any]
) -> SupplementProduct:
    """The product with an admin's corrections; zero amounts are dropped as on import."""
    unknown = sorted(set(changes) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValueError(f"cannot edit {', '.join(unknown)}")
    values: dict[str, Any] = dict(changes)
    for field in ("brand", "name", "unit_label"):
        if isinstance(values.get(field), str):
            values[field] = values[field].strip() or None
    # Only the brand may be cleared; every other field needs a value.
    empty = sorted(f for f, v in values.items() if v is None and f != "brand")
    if empty:
        raise ValueError(f"{', '.join(empty)} cannot be empty")
    if "form" in values:
        values["form"] = SupplementForm(values["form"])
    if "nutrients" in values:
        amounts = dict(values["nutrients"] or {})
        preformed = amounts.pop(PREFORMED_VITAMIN_A_KEY, None)
        values["nutrients"] = nutrients_without_zeros(amounts, preformed)
    if "label_rows" in values:
        values["label_rows"] = tuple(
            LabelRow(
                name=str(row["name"]).strip(),
                amount=row.get("amount"),
                unit=row.get("unit"),
                nutrient=row.get("nutrient"),
            )
            for row in values["label_rows"]
            if row.get("amount") != 0
        )
    return replace(product, **values)


def promoted(product: SupplementProduct) -> SupplementProduct:
    """A person's scan as a shared approved row, in place.

    Their list row and doses keep the same product id, and the label photo
    stays as the row's source; product payloads never include it.
    """
    return replace(product, owner_user_id=None, review_status=ReviewStatus.APPROVED)

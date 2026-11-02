"""Supplement products: shared catalog rows and people's own products."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from src.domain.exceptions.barcode_exceptions import InvalidBarcodeError
from src.domain.model.supplement.dose_math import TRACKED_NUTRIENTS, SupplementNutrients
from src.domain.services.barcode.gtin_normalizer import normalize_gtin


class SupplementForm(StrEnum):
    CAPSULE = "capsule"
    SOFTGEL = "softgel"
    TABLET = "tablet"
    CHEWABLE = "chewable"
    GUMMY = "gummy"
    EFFERVESCENT = "effervescent"
    POWDER = "powder"
    LIQUID = "liquid"
    DROPS = "drops"
    SACHET = "sachet"


class ProductSource(StrEnum):
    CURATED = "curated"
    LABEL_SCAN = "label_scan"
    BARCODE = "barcode"
    DESCRIBE = "describe"


class ReviewStatus(StrEnum):
    PRIVATE = "private"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


def gtin14(raw: str) -> str | None:
    """A barcode as GTIN-14, so UPC-A, EAN-13 and EAN-8 scans match; None when invalid."""
    try:
        return normalize_gtin(raw or "").gtin_14
    except InvalidBarcodeError:
        return None


class SupplementProductNotFoundError(LookupError):
    """The product does not exist or the user may not see it."""


@dataclass(frozen=True)
class LabelRow:
    """One label line as printed; ``nutrient`` is the tracked key it counts toward."""

    name: str
    amount: float | None = None
    unit: str | None = None
    nutrient: str | None = None

    def __post_init__(self) -> None:
        if self.nutrient is not None and self.nutrient not in TRACKED_NUTRIENTS:
            raise ValueError(f"{self.nutrient} is not a tracked supplement nutrient")

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "amount": self.amount,
            "unit": self.unit,
            "nutrient": self.nutrient,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> LabelRow:
        amount = data.get("amount")
        nutrient = data.get("nutrient")
        return cls(
            name=str(data.get("name") or ""),
            amount=float(amount) if isinstance(amount, int | float) else None,
            unit=None if data.get("unit") is None else str(data["unit"]),
            nutrient=str(nutrient) if nutrient in TRACKED_NUTRIENTS else None,
        )


@dataclass(frozen=True)
class SupplementProduct:
    """A product dosed per unit (viên, giọt, ml).

    Visibility: a shared row (no owner) is visible to everyone once approved;
    a person's own row only to its owner. Estimates from a Ghi nhanh sentence
    stay private for good.
    """

    id: str
    name: str
    form: SupplementForm
    unit_label: str
    source: ProductSource
    review_status: ReviewStatus
    owner_user_id: str | None = None
    brand: str | None = None
    name_original: str | None = None
    units_per_serving: float = 1.0
    nutrients: SupplementNutrients = field(default_factory=SupplementNutrients)
    label_rows: tuple[LabelRow, ...] = ()
    barcodes: tuple[str, ...] = ()
    catalog_key: str | None = None
    origin_country: str | None = None
    is_medicine_in_vn: bool = False
    sold_in_vn: bool = False
    source_name: str | None = None
    source_url: str | None = None
    macro_bearing: bool = False
    estimated: bool = False
    label_image_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a supplement product needs a name")
        if not math.isfinite(self.units_per_serving) or self.units_per_serving <= 0:
            raise ValueError("units_per_serving must be more than zero")
        if self.estimated and self.source is not ProductSource.DESCRIBE:
            raise ValueError("only products from a Ghi nhanh sentence are estimates")
        if self.shared and self.review_status is ReviewStatus.PRIVATE:
            raise ValueError("a shared product cannot be private")
        if not self.shared and self.review_status is ReviewStatus.APPROVED:
            raise ValueError("an approved product is shared and has no owner")
        if self.estimated and self.shared:
            raise ValueError("an estimated product is never shared")
        barcodes = tuple(
            dict.fromkeys(code for raw in self.barcodes if (code := gtin14(raw)))
        )
        object.__setattr__(self, "barcodes", barcodes)

    @property
    def shared(self) -> bool:
        return self.owner_user_id is None

    def visible_to(self, user_id: str) -> bool:
        if self.shared:
            return self.review_status is ReviewStatus.APPROVED
        return self.owner_user_id == user_id

"""Requests and responses for the admin supplement catalog and review queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

SupplementFormName = Literal[
    "capsule",
    "softgel",
    "tablet",
    "chewable",
    "gummy",
    "effervescent",
    "powder",
    "liquid",
    "drops",
    "sachet",
]


class AdminSupplementImportRequest(BaseModel):
    """Records shaped like ``build_catalog.py`` output; ``issues`` hold one back."""

    records: list[dict[str, Any]] = Field(..., min_length=1, max_length=500)
    dry_run: bool = False


class AdminSupplementInvalidRecord(BaseModel):
    record: str
    error: str


class AdminSupplementBarcodeSkip(BaseModel):
    catalog_key: str
    barcode: str
    reason: str


class AdminSupplementImportResponse(BaseModel):
    applied: bool
    received: int
    held_back: int
    inserted: int
    updated: int
    unchanged: int
    kept_reviewed: int
    kept_approved: int
    kept_approved_keys: list[str]
    invalid: list[AdminSupplementInvalidRecord]
    barcode_skips: list[AdminSupplementBarcodeSkip]


class AdminSupplementLabelRow(BaseModel):
    name: str
    amount: float | None = None
    unit: str | None = None
    nutrient: str | None = None


class AdminSupplementLabelRowEdit(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    amount: FiniteFloat | None = Field(None, ge=0)
    unit: str | None = Field(None, max_length=32)
    nutrient: str | None = Field(None, max_length=32)


class AdminSupplementEdits(BaseModel):
    """Only the fields sent are changed."""

    model_config = ConfigDict(extra="forbid")

    brand: str | None = Field(None, max_length=255)
    name: str | None = Field(None, min_length=1, max_length=255)
    form: SupplementFormName | None = None
    unit_label: str | None = Field(None, min_length=1, max_length=32)
    units_per_serving: FiniteFloat | None = Field(None, gt=0, le=1000)
    nutrients: dict[str, FiniteFloat] | None = None
    label_rows: list[AdminSupplementLabelRowEdit] | None = Field(None, max_length=100)
    sold_in_vn: bool | None = None
    is_medicine_in_vn: bool | None = None
    # A product logged as food (protein powder, gainer, shake), never as a dose.
    macro_bearing: bool | None = None


class AdminSupplementReviewRequest(BaseModel):
    action: Literal["approve", "edit", "reject"]
    changes: AdminSupplementEdits | None = None


class AdminSupplementBarcodeMatch(BaseModel):
    """The shared row a scan's barcode already belongs to."""

    id: str
    name: str
    brand: str | None = None
    catalog_key: str | None = None
    review_status: str


class AdminSupplementReviewItem(BaseModel):
    id: str
    kind: Literal["catalog", "scan"]
    catalog_key: str | None = None
    brand: str | None = None
    name: str
    name_original: str | None = None
    form: str
    unit_label: str
    units_per_serving: float
    nutrients: dict[str, float]
    label_rows: list[AdminSupplementLabelRow]
    barcodes: list[str]
    origin_country: str | None = None
    sold_in_vn: bool
    is_medicine_in_vn: bool
    macro_bearing: bool
    source: str
    source_name: str | None = None
    source_url: str | None = None
    label_image_url: str | None = None
    # The product photo people see; set through POST /{id}/image.
    image_url: str | None = None
    reasons: list[str]
    review_status: str
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Approved: approving merges into it. Waiting: decide that row first.
    barcode_match: AdminSupplementBarcodeMatch | None = None


class AdminSupplementReviewListResponse(BaseModel):
    items: list[AdminSupplementReviewItem]
    total: int
    limit: int
    offset: int


class AdminSupplementReviewResponse(BaseModel):
    item: AdminSupplementReviewItem
    merged_into: str | None = None

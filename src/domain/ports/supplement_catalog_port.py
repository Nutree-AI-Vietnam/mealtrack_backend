"""Persistence port for the curated supplement catalog and its review queue."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from src.domain.model.supplement import SupplementProduct
from src.domain.model.supplement.catalog_entry import CatalogEntry

ReviewKind = Literal["catalog", "scan"]
QueueStatus = Literal["pending_review", "approved"]


@dataclass(frozen=True)
class ReviewItem:
    """A product with the reasons it was held back and who last reviewed it."""

    product: SupplementProduct
    reasons: tuple[str, ...] = ()
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None


@dataclass(frozen=True)
class BarcodeSkip:
    """A barcode left off a product, and why (another shared product has it)."""

    catalog_key: str
    barcode: str
    reason: str


@dataclass
class CatalogWriteCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    # Rows an admin already edited, approved or rejected; the file no longer owns them.
    kept_reviewed: int = 0
    # Approved rows the file now holds back; people may list them, so they stay.
    kept_approved: int = 0
    kept_approved_keys: list[str] = field(default_factory=list)
    barcode_skips: list[BarcodeSkip] = field(default_factory=list)


class SupplementCatalogPort(ABC):
    """Admin-side writes; they run in the caller's transaction and never commit."""

    @abstractmethod
    async def schema_ready(self) -> bool: ...

    @abstractmethod
    async def upsert_curated(
        self, entries: Sequence[CatalogEntry]
    ) -> CatalogWriteCounts:
        """Insert or update curated rows by catalog key, in order.

        A barcode already on another shared product (or claimed by an earlier
        entry) is skipped and reported. Rows an admin reviewed are left alone,
        and an approved row is never taken back out of the catalog.
        """

    @abstractmethod
    async def review_queue(
        self,
        limit: int,
        offset: int,
        kind: ReviewKind | None = None,
        q: str | None = None,
        status: QueueStatus = "pending_review",
        has_image: bool | None = None,
    ) -> tuple[list[ReviewItem], int]:
        """Products waiting for review (or live, approved ones), oldest first.

        ``q`` matches a catalog key exactly, or part of the name or brand.
        ``has_image`` keeps only products with (True) or without (False) a photo.
        """

    @abstractmethod
    async def review_item(self, product_id: str) -> ReviewItem | None:
        """Any product by id, locked until the transaction ends."""

    @abstractmethod
    async def shared_with_barcode(
        self, barcodes: Sequence[str], exclude_id: str
    ) -> SupplementProduct | None:
        """A shared row carrying one of these barcodes: approved first, else waiting."""

    @abstractmethod
    async def save_review(
        self, product: SupplementProduct, reviewer: str
    ) -> tuple[str, ...]:
        """Store the reviewed product and who reviewed it; returns its barcodes.

        An approved shared row shares its barcodes (a shared holder makes the
        write fail); a rejected shared row gives its barcodes up.
        """

    @abstractmethod
    async def set_image(
        self, product_id: str, image_id: str | None, image_url: str | None
    ) -> None:
        """Point the product at a stored photo, or at none; review fields stay."""

    @abstractmethod
    async def delete_product(self, product_id: str) -> None:
        """Delete a product nothing refers to any more; its barcodes go with it."""

    @abstractmethod
    async def move_to_shared(self, user_id: str, from_id: str, to_id: str) -> None:
        """Move the user's list row and doses to another product.

        A list row already on that product wins, and the moved one is dropped.
        Each dose keeps its own nutrient snapshot.
        """

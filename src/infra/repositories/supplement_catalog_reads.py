"""Review-queue reads of the supplement catalog: waiting or live rows, by id or barcode."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.supplement import ReviewStatus, SupplementProduct
from src.domain.ports.supplement_catalog_port import QueueStatus, ReviewItem, ReviewKind
from src.domain.utils.food_search_text import (
    MAX_FOOD_SEARCH_WORDS,
    clip_food_search_query,
    food_search_words,
    search_word_pattern,
)
from src.infra.database.models.supplement import (
    SupplementProductBarcodeORM as Barcode,
)
from src.infra.database.models.supplement import (
    SupplementProductORM as Product,
)
from src.infra.mappers.supplement_mapper import product_to_domain

logger = logging.getLogger(__name__)


class SupplementCatalogReads:
    """Queue and lookup reads shared by the catalog repository."""

    session: AsyncSession

    async def review_queue(
        self,
        limit: int,
        offset: int,
        kind: ReviewKind | None = None,
        q: str | None = None,
        status: QueueStatus = "pending_review",
        has_image: bool | None = None,
    ) -> tuple[list[ReviewItem], int]:
        waiting = [Product.review_status == status]
        if kind == "catalog":
            waiting.append(Product.owner_user_id.is_(None))
        elif kind == "scan":
            waiting.append(Product.owner_user_id.is_not(None))
        if has_image is not None:
            waiting.append(
                Product.image_url.is_not(None)
                if has_image
                else Product.image_url.is_(None)
            )
        if q:
            # Words fold like product search, so "now iron" finds NOW Iron 18 mg.
            words = food_search_words(clip_food_search_query(q))
            words = words[:MAX_FOOD_SEARCH_WORDS]
            named = (
                and_(*(Product.name_search.like(search_word_pattern(w)) for w in words))
                if words
                else false()
            )
            waiting.append(or_(Product.catalog_key == q, named))
        total = await self.session.scalar(
            select(func.count()).select_from(Product).where(*waiting)
        )
        rows = await self.session.execute(
            select(Product)
            .where(*waiting)
            .order_by(Product.created_at, Product.id)
            .limit(limit)
            .offset(offset)
        )
        return await self._items(rows.scalars().all()), int(total or 0)

    async def review_item(self, product_id: str) -> ReviewItem | None:
        rows = await self.session.execute(
            select(Product).where(Product.id == product_id).with_for_update()
        )
        items = await self._items(rows.scalars().all())
        return items[0] if items else None

    async def shared_with_barcode(
        self, barcodes: Sequence[str], exclude_id: str
    ) -> SupplementProduct | None:
        if not barcodes:
            return None
        approved = Product.review_status == ReviewStatus.APPROVED.value
        rows = await self.session.execute(
            select(Product)
            .join(Barcode, Barcode.product_id == Product.id)
            .where(
                Barcode.barcode.in_(list(barcodes)),
                Barcode.owner_user_id.is_(None),
                Product.review_status.in_(
                    [ReviewStatus.APPROVED.value, ReviewStatus.PENDING_REVIEW.value]
                ),
                Product.id != exclude_id,
            )
            .order_by(approved.desc(), Product.created_at, Product.id)
            .limit(1)
        )
        items = await self._items(rows.scalars().all())
        return items[0].product if items else None

    async def _items(self, rows: Sequence[Any]) -> list[ReviewItem]:
        if not rows:
            return []
        codes: dict[str, list[str]] = {}
        found = await self.session.execute(
            select(Barcode.product_id, Barcode.barcode).where(
                Barcode.product_id.in_([row.id for row in rows])
            )
        )
        for product_id, code in found.all():
            codes.setdefault(product_id, []).append(code)
        items = []
        for row in rows:
            try:
                product = product_to_domain(row, codes.get(row.id, []))
            except ValueError:
                logger.warning(
                    "Skipping invalid supplement product %s", row.id, exc_info=True
                )
                continue
            items.append(
                ReviewItem(
                    product=product,
                    reasons=tuple(row.review_reasons or ()),
                    reviewed_at=row.reviewed_at,
                    reviewed_by=row.reviewed_by,
                )
            )
        return items

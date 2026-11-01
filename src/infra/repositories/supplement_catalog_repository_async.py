"""The curated supplement catalog and its review queue in PostgreSQL."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.supplement import ReviewStatus, SupplementProduct
from src.domain.model.supplement.catalog_entry import CatalogEntry
from src.domain.ports.supplement_catalog_port import (
    CatalogWriteCounts,
    ReviewItem,
    ReviewKind,
    SupplementCatalogPort,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.supplement import (
    SupplementIntakeORM as Intake,
)
from src.infra.database.models.supplement import (
    SupplementProductBarcodeORM as Barcode,
)
from src.infra.database.models.supplement import (
    SupplementProductORM as Product,
)
from src.infra.database.models.supplement import (
    UserSupplementORM as ListEntry,
)
from src.infra.mappers.supplement_mapper import product_to_domain, product_values
from src.infra.repositories.supplement_catalog_upsert import (
    NO_SYNC,
    upsert_curated_rows,
)
from src.infra.repositories.supplement_repository_async import (
    AsyncSupplementRepository,
)

logger = logging.getLogger(__name__)


class AsyncSupplementCatalogRepository(SupplementCatalogPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def schema_ready(self) -> bool:
        return await AsyncSupplementRepository(self.session).schema_ready()

    async def upsert_curated(
        self, entries: Sequence[CatalogEntry]
    ) -> CatalogWriteCounts:
        return await upsert_curated_rows(self.session, entries)

    async def review_queue(
        self,
        limit: int,
        offset: int,
        kind: ReviewKind | None = None,
        q: str | None = None,
    ) -> tuple[list[ReviewItem], int]:
        waiting = [Product.review_status == ReviewStatus.PENDING_REVIEW.value]
        if kind == "catalog":
            waiting.append(Product.owner_user_id.is_(None))
        elif kind == "scan":
            waiting.append(Product.owner_user_id.is_not(None))
        if q:
            waiting.append(
                or_(
                    Product.catalog_key == q,
                    Product.name.icontains(q, autoescape=True),
                    Product.brand.icontains(q, autoescape=True),
                )
            )
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

    async def save_review(
        self, product: SupplementProduct, reviewer: str
    ) -> tuple[str, ...]:
        values = product_values(product)
        values.pop("id")
        await self.session.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(**values, reviewed_at=utc_now(), reviewed_by=reviewer)
            .execution_options(**NO_SYNC)
        )
        if not product.shared:
            return product.barcodes
        if product.review_status is ReviewStatus.REJECTED:
            # A hidden row must not keep codes a later product needs.
            await self.session.execute(
                delete(Barcode)
                .where(Barcode.product_id == product.id)
                .execution_options(**NO_SYNC)
            )
            return ()
        if product.review_status is not ReviewStatus.APPROVED:
            return product.barcodes
        # Review settles any shared holder first (merge, duplicate or wait), so
        # a holder here is a concurrent review and the unique index refuses this.
        kept = await self.session.execute(
            update(Barcode)
            .where(Barcode.product_id == product.id)
            .values(owner_user_id=None)
            .returning(Barcode.barcode)
            .execution_options(**NO_SYNC)
        )
        return tuple(sorted(kept.scalars().all()))

    async def move_to_shared(self, user_id: str, from_id: str, to_id: str) -> None:
        mine = (ListEntry.user_id == user_id, ListEntry.product_id == from_id)
        duplicate = await self.session.scalar(
            select(ListEntry.id).where(
                ListEntry.user_id == user_id, ListEntry.product_id == to_id
            )
        )
        if duplicate is not None:
            statement: Any = delete(ListEntry).where(*mine)
        else:
            statement = update(ListEntry).where(*mine).values(product_id=to_id)
        await self.session.execute(statement.execution_options(**NO_SYNC))
        # Doses follow, so the list row still shows what was taken today.
        await self.session.execute(
            update(Intake)
            .where(Intake.user_id == user_id, Intake.product_id == from_id)
            .values(product_id=to_id)
            .execution_options(**NO_SYNC)
        )

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

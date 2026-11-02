"""The curated supplement catalog and its review queue in PostgreSQL."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.supplement import ReviewStatus, SupplementProduct
from src.domain.model.supplement.catalog_entry import CatalogEntry
from src.domain.ports.supplement_catalog_port import (
    CatalogWriteCounts,
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
from src.infra.mappers.supplement_mapper import product_values
from src.infra.repositories.supplement_catalog_reads import SupplementCatalogReads
from src.infra.repositories.supplement_catalog_upsert import (
    NO_SYNC,
    upsert_curated_rows,
)
from src.infra.repositories.supplement_repository_async import (
    AsyncSupplementRepository,
)

logger = logging.getLogger(__name__)


class AsyncSupplementCatalogRepository(SupplementCatalogReads, SupplementCatalogPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def schema_ready(self) -> bool:
        return await AsyncSupplementRepository(self.session).schema_ready()

    async def upsert_curated(
        self, entries: Sequence[CatalogEntry]
    ) -> CatalogWriteCounts:
        return await upsert_curated_rows(self.session, entries)

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

    async def delete_product(self, product_id: str) -> None:
        await self.session.execute(
            delete(Product).where(Product.id == product_id).execution_options(**NO_SYNC)
        )

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

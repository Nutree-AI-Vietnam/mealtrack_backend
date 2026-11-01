"""Supplement products, lists and doses in PostgreSQL."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.domain.model.supplement import (
    ReviewStatus,
    SupplementIntake,
    SupplementProduct,
    SupplementProductNotFoundError,
    UserSupplement,
    gtin14,
)
from src.domain.ports.supplement_repository_port import SupplementRepositoryPort
from src.domain.utils.food_search_text import (
    MAX_FOOD_SEARCH_WORDS,
    clip_food_search_query,
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
from src.infra.mappers.supplement_mapper import (
    intake_to_domain,
    intake_values,
    list_entry_to_domain,
    product_to_domain,
    product_values,
)

logger = logging.getLogger(__name__)

_MAX_SEARCH_LIMIT = 50
_TABLES = (
    "supplement_products",
    "supplement_product_barcodes",
    "user_supplements",
    "supplement_intakes",
)


def _visible_to(user_id: str) -> ColumnElement[bool]:
    # An empty id would compare as "owner IS NULL" and expose unapproved shared rows.
    if not user_id:
        raise ValueError("a visibility check needs a user id")
    return or_(
        and_(
            Product.owner_user_id.is_(None),
            Product.review_status == ReviewStatus.APPROVED.value,
        ),
        Product.owner_user_id == user_id,
    )


def _like(word: str) -> str:
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class AsyncSupplementRepository(SupplementRepositoryPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def schema_ready(self) -> bool:
        checks = " AND ".join(f"to_regclass('{t}') IS NOT NULL" for t in _TABLES)
        return bool((await self.session.execute(text(f"SELECT {checks}"))).scalar_one())

    async def get_product(
        self, product_id: str, user_id: str
    ) -> SupplementProduct | None:
        rows = await self._products(
            select(Product).where(Product.id == product_id, _visible_to(user_id))
        )
        return rows[0] if rows else None

    async def find_by_barcode(
        self, barcode: str, user_id: str
    ) -> SupplementProduct | None:
        code = gtin14(barcode)
        if code is None:
            return None
        rows = await self._products(
            select(Product)
            .join(Barcode, Barcode.product_id == Product.id)
            .where(Barcode.barcode == code, _visible_to(user_id))
            .order_by(Product.owner_user_id.is_(None).desc())
            .limit(1)
        )
        return rows[0] if rows else None

    async def search_products(
        self, query: str, user_id: str, limit: int = 20
    ) -> list[SupplementProduct]:
        words = clip_food_search_query(query).split()[:MAX_FOOD_SEARCH_WORDS]
        if not words:
            return []
        haystack = func.concat_ws(
            " ", Product.brand, Product.name, Product.name_original
        )
        return await self._products(
            select(Product)
            .where(_visible_to(user_id), *(haystack.ilike(_like(w)) for w in words))
            .order_by(
                Product.owner_user_id.is_(None).desc(),
                Product.sold_in_vn.desc(),
                Product.brand,
                Product.name,
            )
            .limit(max(1, min(limit, _MAX_SEARCH_LIMIT)))
        )

    async def add_product(self, product: SupplementProduct) -> tuple[str, ...]:
        await self.session.execute(pg_insert(Product).values(**product_values(product)))
        if not product.barcodes:
            return ()
        # A barcode already on another shared product, or on another of the
        # owner's products, stays with that product instead of failing the write.
        stored = await self.session.execute(
            pg_insert(Barcode)
            .values(
                [
                    {
                        "product_id": product.id,
                        "barcode": code,
                        "owner_user_id": product.owner_user_id,
                    }
                    for code in product.barcodes
                ]
            )
            .on_conflict_do_nothing()
            .returning(Barcode.barcode)
        )
        return tuple(sorted(stored.scalars().all()))

    async def list_supplements(self, user_id: str) -> list[UserSupplement]:
        rows = await self.session.execute(
            select(ListEntry)
            .where(ListEntry.user_id == user_id)
            .order_by(ListEntry.sort_order, ListEntry.created_at)
        )
        return [list_entry_to_domain(row) for row in rows.scalars().all()]

    async def save_list_entry(self, entry: UserSupplement) -> UserSupplement:
        await self._require_visible(entry.product_id, entry.user_id)
        changes = {
            "units_per_dose": entry.units_per_dose,
            "active": entry.active,
            "sort_order": entry.sort_order,
            "updated_at": utc_now(),
        }
        statement = (
            pg_insert(ListEntry)
            .values(
                id=entry.id,
                user_id=entry.user_id,
                product_id=entry.product_id,
                **changes,
            )
            .on_conflict_do_update(
                index_elements=[ListEntry.user_id, ListEntry.product_id], set_=changes
            )
            .returning(ListEntry)
            .execution_options(populate_existing=True)
        )
        row = (await self.session.execute(statement)).scalar_one()
        return list_entry_to_domain(row)

    async def remove_list_entry(self, user_id: str, entry_id: str) -> bool:
        return bool(
            await self._rowcount(
                delete(ListEntry).where(
                    ListEntry.id == entry_id, ListEntry.user_id == user_id
                )
            )
        )

    async def add_intake(self, intake: SupplementIntake) -> bool:
        await self._require_visible(intake.product_id, intake.user_id)
        statement = (
            pg_insert(Intake)
            .values(**intake_values(intake))
            .on_conflict_do_nothing(index_elements=[Intake.id])
            .returning(Intake.id)
        )
        return (await self.session.execute(statement)).scalar_one_or_none() is not None

    async def intakes_between(
        self, user_id: str, start: date, end: date
    ) -> list[SupplementIntake]:
        rows = await self.session.execute(
            select(Intake)
            .where(
                Intake.user_id == user_id,
                Intake.local_date.between(start, end),
                Intake.deleted_at.is_(None),
            )
            .order_by(Intake.taken_at)
        )
        return [intake_to_domain(row) for row in rows.scalars().all()]

    async def soft_delete_intake(
        self, user_id: str, intake_id: str, deleted_at: datetime
    ) -> bool:
        return bool(
            await self._rowcount(
                update(Intake)
                .where(
                    Intake.id == intake_id,
                    Intake.user_id == user_id,
                    Intake.deleted_at.is_(None),
                )
                .values(deleted_at=deleted_at, updated_at=deleted_at)
            )
        )

    async def delete_user_data(self, user_id: str) -> int:
        removed = 0
        for statement in (
            delete(Intake).where(Intake.user_id == user_id),
            delete(ListEntry).where(ListEntry.user_id == user_id),
            delete(Product).where(Product.owner_user_id == user_id),
        ):
            removed += await self._rowcount(statement)
        return removed

    async def _require_visible(self, product_id: str, user_id: str) -> None:
        # Another user's private product must never gain doses or list rows:
        # its owner's account deletion would then be blocked or lose their rows.
        found = await self.session.execute(
            select(Product.id).where(Product.id == product_id, _visible_to(user_id))
        )
        if found.scalar_one_or_none() is None:
            raise SupplementProductNotFoundError(product_id)

    async def _rowcount(self, statement: Any) -> int:
        # DML results are cursor results, which carry the affected row count.
        result = cast(CursorResult[Any], await self.session.execute(statement))
        return int(result.rowcount or 0)

    async def _products(self, statement: Any) -> list[SupplementProduct]:
        rows = (await self.session.execute(statement)).scalars().all()
        if not rows:
            return []
        codes: dict[str, list[str]] = {}
        for product_id, code in (
            await self.session.execute(
                select(Barcode.product_id, Barcode.barcode).where(
                    Barcode.product_id.in_([row.id for row in rows])
                )
            )
        ).all():
            codes.setdefault(product_id, []).append(code)
        products = []
        for row in rows:
            try:
                products.append(product_to_domain(row, codes.get(row.id, [])))
            except ValueError:
                # One malformed row must not break every search that returns it.
                logger.warning(
                    "Skipping invalid supplement product %s", row.id, exc_info=True
                )
        return products

"""Supplement products, lists and doses in PostgreSQL."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.supplement import (
    SupplementIntake,
    SupplementProduct,
    UserSupplement,
)
from src.domain.ports.supplement_repository_port import SupplementRepositoryPort
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
    product_values,
)
from src.infra.repositories.supplement_product_reads import SupplementProductReads

_DATA_TABLES = (
    "supplement_products",
    "supplement_product_barcodes",
    "user_supplements",
    "supplement_intakes",
)
_TABLES = (
    *_DATA_TABLES,
    # Every product read selects name_search, so its migration must have run too.
    "ix_supplement_products_name_search_trgm",
)
# Every product read selects the review and photo fields too.
_PRODUCT_COLUMNS = (
    "review_reasons",
    "reviewed_at",
    "reviewed_by",
    "image_id",
    "image_url",
)


logger = logging.getLogger(__name__)

# A positive answer is final for the process: tables are never dropped while
# it runs. A negative one is checked again on the next call.
_schema_ready = False


class AsyncSupplementRepository(SupplementProductReads, SupplementRepositoryPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def schema_ready(self) -> bool:
        global _schema_ready
        if not _schema_ready:
            checks = " AND ".join(
                [f"to_regclass('{t}') IS NOT NULL" for t in _TABLES]
                + [
                    "EXISTS (SELECT 1 FROM pg_attribute"
                    " WHERE attrelid = to_regclass('supplement_products')"
                    f" AND attname = '{column}' AND NOT attisdropped)"
                    for column in _PRODUCT_COLUMNS
                ]
            )
            result = await self.session.execute(text(f"SELECT {checks}"))
            _schema_ready = bool(result.scalar_one())
        return _schema_ready

    async def tables_exist(self) -> bool:
        checks = " AND ".join(
            f"to_regclass('{table}') IS NOT NULL" for table in _DATA_TABLES
        )
        result = await self.session.execute(text(f"SELECT {checks}"))
        return bool(result.scalar_one())

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

    async def update_list_entry(
        self,
        user_id: str,
        entry_id: str,
        *,
        units_per_dose: float | None = None,
        active: bool | None = None,
        sort_order: int | None = None,
    ) -> UserSupplement | None:
        changes: dict[str, Any] = {
            key: value
            for key, value in (
                ("units_per_dose", units_per_dose),
                ("active", active),
                ("sort_order", sort_order),
            )
            if value is not None
        }
        changes["updated_at"] = utc_now()
        row = (
            await self.session.execute(
                update(ListEntry)
                .where(ListEntry.id == entry_id, ListEntry.user_id == user_id)
                .values(**changes)
                .returning(ListEntry)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return None if row is None else list_entry_to_domain(row)

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
        doses = []
        for row in rows.scalars().all():
            try:
                doses.append(intake_to_domain(row))
            except ValueError:
                # One malformed snapshot must not take down Home or Trends.
                logger.warning(
                    "Skipping invalid supplement dose %s", row.id, exc_info=True
                )
        return doses

    async def soft_delete_intake(
        self, user_id: str, intake_id: str, deleted_at: datetime
    ) -> SupplementIntake | None:
        row = (
            await self.session.execute(
                update(Intake)
                .where(
                    Intake.id == intake_id,
                    Intake.user_id == user_id,
                    Intake.deleted_at.is_(None),
                )
                .values(deleted_at=deleted_at, updated_at=deleted_at)
                .returning(Intake)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return None if row is None else intake_to_domain(row)

    async def max_updated_at_for_local_day(
        self, target_date: date, user_id: str, user_tz: str | None = None
    ) -> datetime | None:
        # local_date is stored at log time, so the zone is not needed here.
        result = await self.session.execute(
            select(func.max(Intake.updated_at)).where(
                Intake.user_id == user_id, Intake.local_date == target_date
            )
        )
        return result.scalar_one_or_none()

    async def log_days(self, user_id: str) -> list[tuple[date, bool]]:
        result = await self.session.execute(
            select(Intake.local_date, func.bool_or(Intake.logged_on_day))
            .where(Intake.user_id == user_id, Intake.deleted_at.is_(None))
            .group_by(Intake.local_date)
        )
        return [(day, bool(on_day)) for day, on_day in result.all()]

    async def delete_user_data(self, user_id: str) -> int:
        removed = 0
        for statement in (
            delete(Intake).where(Intake.user_id == user_id),
            delete(ListEntry).where(ListEntry.user_id == user_id),
            delete(Product).where(Product.owner_user_id == user_id),
        ):
            removed += await self._rowcount(statement)
        return removed

    async def _rowcount(self, statement: Any) -> int:
        # DML results are cursor results, which carry the affected row count.
        result = cast(CursorResult[Any], await self.session.execute(statement))
        return int(result.rowcount or 0)

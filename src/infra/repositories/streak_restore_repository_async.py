"""Streak restore ledger stored in ``streak_restore_ledger``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.streak import LedgerEntry, LedgerKind, LedgerSummary
from src.domain.ports.streak_restore_repository_port import (
    StreakRestoreRepositoryPort,
)
from src.infra.database.models.streak_restore import (
    StreakRestoreLedgerORM as Ledger,
)
from src.infra.database.models.user.user import User

_DAY_KINDS = (LedgerKind.SPEND, LedgerKind.FREE)


class AsyncStreakRestoreRepository(StreakRestoreRepositoryPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_user(self, user_id: str) -> None:
        await self.session.execute(
            select(User.id).where(User.id == user_id).with_for_update()
        )

    async def summary(self, user_id: str) -> LedgerSummary:
        rows = (
            await self.session.execute(
                select(Ledger.kind, Ledger.delta, Ledger.restored_date).where(
                    Ledger.user_id == user_id
                )
            )
        ).all()
        return LedgerSummary(
            balance=sum(delta for _, delta, _ in rows),
            free_used=any(kind == LedgerKind.FREE for kind, _, _ in rows),
            restored_dates=frozenset(
                day for kind, _, day in rows if kind in _DAY_KINDS and day
            ),
        )

    async def balance(self, user_id: str) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.sum(Ledger.delta), 0)).where(
                Ledger.user_id == user_id
            )
        )
        return int(result.scalar_one())

    async def find_transaction(
        self, store_transaction_id: str, kind: LedgerKind
    ) -> LedgerEntry | None:
        row = (
            await self.session.execute(
                select(Ledger).where(
                    Ledger.store_transaction_id == store_transaction_id,
                    Ledger.kind == kind.value,
                )
            )
        ).scalar_one_or_none()
        return None if row is None else _to_entry(row)

    async def insert_entry(self, entry: LedgerEntry) -> bool:
        statement = pg_insert(Ledger).values(
            id=entry.id,
            user_id=entry.user_id,
            kind=entry.kind.value,
            delta=entry.delta,
            product_id=entry.product_id,
            store=entry.store,
            store_transaction_id=entry.store_transaction_id,
            environment=entry.environment,
            restored_date=entry.restored_date,
        )
        # Only the unique index of the row's own kind may swallow a conflict;
        # any other rule broken (a second free restore) raises.
        if entry.kind in _DAY_KINDS:
            statement = statement.on_conflict_do_nothing(
                index_elements=[Ledger.user_id, Ledger.restored_date],
                index_where=text("kind IN ('spend', 'free')"),
            )
        else:
            statement = statement.on_conflict_do_nothing(
                index_elements=[Ledger.store_transaction_id, Ledger.kind],
                index_where=text("store_transaction_id IS NOT NULL"),
            )
        result = await self.session.execute(statement.returning(Ledger.id))
        return result.scalar_one_or_none() is not None


def _to_entry(row: Any) -> LedgerEntry:
    return LedgerEntry(
        id=row.id,
        user_id=row.user_id,
        kind=LedgerKind(row.kind),
        delta=row.delta,
        product_id=row.product_id,
        store=row.store,
        store_transaction_id=row.store_transaction_id,
        environment=row.environment,
        restored_date=row.restored_date,
    )

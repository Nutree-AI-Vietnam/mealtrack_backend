"""In-memory streak restore ledger with the table's unique rules."""

from src.domain.model.streak import LedgerEntry, LedgerKind, LedgerSummary
from src.domain.ports.streak_restore_repository_port import (
    StreakRestoreRepositoryPort,
)


class InMemoryLedger(StreakRestoreRepositoryPort):
    """Keeps the table's unique rules: one row per transaction and kind,
    one per restored day, one free restore per user."""

    def __init__(self):
        self.rows: list[LedgerEntry] = []
        self.locked: list[str] = []
        self.events: list[tuple[str, str]] = []

    async def lock_user(self, user_id):
        self.locked.append(user_id)
        self.events.append(("lock", user_id))

    async def summary(self, user_id):
        mine = [row for row in self.rows if row.user_id == user_id]
        return LedgerSummary(
            balance=sum(row.delta for row in mine),
            free_used=any(row.kind is LedgerKind.FREE for row in mine),
            restored_dates=frozenset(
                row.restored_date for row in mine if row.restored_date
            ),
        )

    async def balance(self, user_id):
        return (await self.summary(user_id)).balance

    async def find_transaction(self, store_transaction_id, kind):
        return next(
            (
                row
                for row in self.rows
                if row.store_transaction_id == store_transaction_id and row.kind is kind
            ),
            None,
        )

    async def insert_entry(self, entry):
        if entry.kind in (LedgerKind.SPEND, LedgerKind.FREE):
            if (
                entry.restored_date
                in (await self.summary(entry.user_id)).restored_dates
            ):
                return False
            if (
                entry.kind is LedgerKind.FREE
                and (await self.summary(entry.user_id)).free_used
            ):
                raise AssertionError("second free restore")
        elif await self.find_transaction(entry.store_transaction_id, entry.kind):
            return False
        self.rows.append(entry)
        self.events.append((entry.kind.value, entry.user_id))
        return True

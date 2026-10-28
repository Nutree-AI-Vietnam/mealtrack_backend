"""Persistence port for the streak restore ledger."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.domain.model.streak import LedgerEntry, LedgerKind, LedgerSummary


class StreakRestoreRepositoryPort(ABC):
    """Writes run in the caller's transaction; nothing here commits."""

    @abstractmethod
    async def lock_user(self, user_id: str) -> None:
        """Hold the user's row until commit, so balance checks and writes serialize."""

    @abstractmethod
    async def summary(self, user_id: str) -> LedgerSummary:
        """Balance, whether the free restore is used, and every restored day."""

    @abstractmethod
    async def balance(self, user_id: str) -> int:
        """Paid restores the user holds."""

    @abstractmethod
    async def find_transaction(
        self, store_transaction_id: str, kind: LedgerKind
    ) -> LedgerEntry | None:
        """The row of this kind for a store transaction, whoever owns it."""

    @abstractmethod
    async def insert_entry(self, entry: LedgerEntry) -> bool:
        """Insert unless the transaction or restored day already has its row.

        False means the row already existed. Any other rule broken (a second
        free restore) raises.
        """

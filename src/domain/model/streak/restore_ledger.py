"""Rows of the streak restore ledger: credits, refunds and restored days."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from uuid import uuid4


class LedgerKind(StrEnum):
    PURCHASE = "purchase"  # a pack bought, +count
    REFUND = "refund"  # a pack refunded, -count floored at the balance
    SPEND = "spend"  # a paid restore spent on a day, -1
    FREE = "free"  # the one free restore spent on a day, 0


@dataclass(frozen=True)
class LedgerEntry:
    user_id: str
    kind: LedgerKind
    delta: int
    product_id: str | None = None
    store: str | None = None
    store_transaction_id: str | None = None
    environment: str | None = None
    restored_date: date | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class LedgerSummary:
    """A user's ledger: paid restores left, the free one, and restored days."""

    balance: int
    free_used: bool
    restored_dates: frozenset[date]


class NotEnoughRestoresError(Exception):
    """The user holds fewer restores than the days to restore."""

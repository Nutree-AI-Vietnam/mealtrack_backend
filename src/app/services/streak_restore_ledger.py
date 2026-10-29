"""Credits, refunds and spends on the streak restore ledger.

Each call runs in the caller's unit of work and leaves the commit to it.
Paths that read the balance lock the user's row first, so two spends or a
spend and a refund never act on the same balance.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from src.domain.model.streak import LedgerEntry, LedgerKind, NotEnoughRestoresError
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PackPurchase:
    product_id: str
    store_transaction_id: str
    count: int  # restore_count_for(product_id)
    store: str | None = None
    environment: str | None = None


@dataclass(frozen=True)
class CreditResult:
    """Who holds a transaction's restores, how many, and whether this call wrote them."""

    owner_id: str
    count: int
    inserted: bool


async def credit_pack(
    uow: AsyncUnitOfWorkPort, user_id: str, purchase: PackPurchase
) -> CreditResult:
    """Credit a pack once per store transaction.

    A transaction already credited stays with the user who first claimed or
    received it, whoever asks now.
    """
    repo = uow.streak_restores
    existing = await repo.find_transaction(
        purchase.store_transaction_id, LedgerKind.PURCHASE
    )
    if existing is None:
        inserted = await repo.insert_entry(
            _purchase_row(LedgerKind.PURCHASE, user_id, purchase, purchase.count)
        )
        if inserted:
            return CreditResult(user_id, purchase.count, True)
        # Another request credited it between the read and the insert.
        existing = await repo.find_transaction(
            purchase.store_transaction_id, LedgerKind.PURCHASE
        )
    if existing is None:
        raise RuntimeError("streak restore credit vanished after a conflict")
    return CreditResult(existing.user_id, existing.delta, False)


async def refund_pack(
    uow: AsyncUnitOfWorkPort, event_user_id: str, purchase: PackPurchase
) -> CreditResult:
    """Take a refunded pack back from its owner, never below a zero balance.

    A refund that arrives before its credit writes the credit first, so the two
    net to zero and a later claim adds nothing. ``count`` in the result is the
    number of restores taken back.
    """
    repo = uow.streak_restores
    refunded = await repo.find_transaction(
        purchase.store_transaction_id, LedgerKind.REFUND
    )
    if refunded is not None:
        return CreditResult(refunded.user_id, 0, False)
    # Lock before writing the credit, in the order a claim takes (user, then
    # transaction), so a refund racing a claim never deadlocks.
    purchased = await repo.find_transaction(
        purchase.store_transaction_id, LedgerKind.PURCHASE
    )
    owner_id = purchased.user_id if purchased else event_user_id
    await repo.lock_user(owner_id)
    credit = await credit_pack(uow, owner_id, purchase)
    if credit.owner_id != owner_id:
        # Another user's claim committed the credit while this waited.
        owner_id = credit.owner_id
        await repo.lock_user(owner_id)
    if owner_id != event_user_id:
        logger.warning(
            "streak_restore_refund_owner_mismatch transaction=%s owner=%s event_user=%s",
            purchase.store_transaction_id,
            owner_id,
            event_user_id,
        )
    taken = min(credit.count, max(await repo.balance(owner_id), 0))
    # A zero row still records the refund, so a retried event changes nothing.
    inserted = await repo.insert_entry(
        _purchase_row(LedgerKind.REFUND, owner_id, purchase, -taken)
    )
    return CreditResult(owner_id, taken if inserted else 0, inserted)


async def spend_restores(
    uow: AsyncUnitOfWorkPort, user_id: str, dates: Sequence[date]
) -> list[date]:
    """Restore the given days: the free restore first, then paid ones.

    Days already restored are skipped and cost nothing. Returns the days
    restored by this call. The caller checks the days against the offer.
    """
    repo = uow.streak_restores
    await repo.lock_user(user_id)
    ledger = await repo.summary(user_id)
    todo = sorted(set(dates) - ledger.restored_dates)
    free = 0 if ledger.free_used else 1
    if len(todo) - free > ledger.balance:
        raise NotEnoughRestoresError
    restored: list[date] = []
    for index, day in enumerate(todo):
        kind = LedgerKind.FREE if index < free else LedgerKind.SPEND
        entry = LedgerEntry(
            user_id=user_id,
            kind=kind,
            delta=0 if kind is LedgerKind.FREE else -1,
            restored_date=day,
        )
        if await repo.insert_entry(entry):
            restored.append(day)
    return restored


def _purchase_row(
    kind: LedgerKind, user_id: str, purchase: PackPurchase, delta: int
) -> LedgerEntry:
    return LedgerEntry(
        user_id=user_id,
        kind=kind,
        delta=delta,
        product_id=purchase.product_id,
        store=purchase.store,
        store_transaction_id=purchase.store_transaction_id,
        environment=purchase.environment,
    )

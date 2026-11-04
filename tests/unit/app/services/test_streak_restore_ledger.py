"""Credits, refunds and spends on the restore ledger (in-memory ledger)."""

from datetime import date
from types import SimpleNamespace

import pytest
from tests.fixtures.fakes.fake_streak_restore_ledger import InMemoryLedger

from src.app.services.streak_restore_ledger import (
    PackPurchase,
    credit_pack,
    refund_pack,
    spend_restores,
)
from src.domain.model.streak import LedgerKind, NotEnoughRestoresError

DAY_1, DAY_2 = date(2026, 10, 26), date(2026, 10, 27)


@pytest.fixture
def uow():
    return SimpleNamespace(streak_restores=InMemoryLedger())


def _pack(count=3, transaction="tx-1", store="app_store"):
    return PackPurchase(
        product_id=f"nutree_streak_restore_{count}",
        store_transaction_id=transaction,
        count=count,
        store=store,
        environment="production",
    )


@pytest.mark.asyncio
async def test_a_transaction_is_credited_once_across_stores(uow):
    first = await credit_pack(uow, "u1", _pack(store="app_store"))
    again = await credit_pack(uow, "u1", _pack(store=None))
    assert (first.inserted, first.count) == (True, 3)
    assert (again.inserted, again.owner_id, again.count) == (False, "u1", 3)
    assert await uow.streak_restores.balance("u1") == 3


@pytest.mark.asyncio
async def test_someone_elses_transaction_returns_its_owner_and_credits_nothing(uow):
    await credit_pack(uow, "owner", _pack())
    result = await credit_pack(uow, "other", _pack())
    assert (result.owner_id, result.inserted) == ("owner", False)
    assert await uow.streak_restores.balance("other") == 0


@pytest.mark.asyncio
async def test_refund_takes_back_no_more_than_the_balance(uow):
    await credit_pack(uow, "u1", _pack(count=3))
    await spend_restores(uow, "u1", [DAY_1, DAY_2])  # free + 1 paid
    result = await refund_pack(uow, "u1", _pack(count=3))
    assert result.count == 2
    assert await uow.streak_restores.balance("u1") == 0


@pytest.mark.asyncio
async def test_refund_before_credit_nets_zero_and_a_later_claim_adds_nothing(uow):
    await refund_pack(uow, "u1", _pack(count=3))
    claim = await credit_pack(uow, "u1", _pack(count=3))
    assert claim.inserted is False
    assert await uow.streak_restores.balance("u1") == 0
    kinds = sorted(row.kind for row in uow.streak_restores.rows)
    assert kinds == [LedgerKind.PURCHASE, LedgerKind.REFUND]


@pytest.mark.asyncio
async def test_refund_debits_the_owner_not_the_event_user(uow):
    await credit_pack(uow, "owner", _pack(count=3))
    result = await refund_pack(uow, "other", _pack(count=3))
    assert result.owner_id == "owner"
    assert await uow.streak_restores.balance("owner") == 0
    assert await uow.streak_restores.balance("other") == 0


@pytest.mark.asyncio
async def test_a_repeated_refund_changes_nothing(uow):
    await credit_pack(uow, "u1", _pack(count=3))
    await refund_pack(uow, "u1", _pack(count=3))
    again = await refund_pack(uow, "u1", _pack(count=3))
    assert (again.inserted, again.count) == (False, 0)
    assert await uow.streak_restores.balance("u1") == 0


@pytest.mark.asyncio
async def test_the_free_restore_goes_first_then_paid_ones(uow):
    await credit_pack(uow, "u1", _pack(count=1))
    restored = await spend_restores(uow, "u1", [DAY_2, DAY_1])
    assert restored == [DAY_1, DAY_2]
    by_day = {
        row.restored_date: row.kind
        for row in uow.streak_restores.rows
        if row.restored_date
    }
    assert by_day == {DAY_1: LedgerKind.FREE, DAY_2: LedgerKind.SPEND}
    assert await uow.streak_restores.balance("u1") == 0
    assert uow.streak_restores.locked == ["u1"]


@pytest.mark.asyncio
async def test_spending_more_than_held_restores_nothing(uow):
    await spend_restores(uow, "u1", [DAY_1])  # uses the free one
    with pytest.raises(NotEnoughRestoresError):
        await spend_restores(uow, "u1", [DAY_2])
    assert len(uow.streak_restores.rows) == 1


@pytest.mark.asyncio
async def test_an_already_restored_day_costs_nothing(uow):
    await credit_pack(uow, "u1", _pack(count=1))
    await spend_restores(uow, "u1", [DAY_1])
    assert await spend_restores(uow, "u1", [DAY_1]) == []
    assert await uow.streak_restores.balance("u1") == 1


@pytest.mark.asyncio
async def test_refund_locks_the_owner_before_writing_any_row(uow):
    await refund_pack(uow, "u1", _pack(count=3))
    assert uow.streak_restores.events == [
        ("lock", "u1"),
        ("purchase", "u1"),
        ("refund", "u1"),
    ]

"""RevenueCat webhook events for streak restore packs (in-memory ledger)."""

from contextlib import ExitStack, contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.v1.webhook_streak_restore import handle_streak_restore_event
from src.api.routes.v1.webhook_subscription_lifecycle import handle_cancellation
from src.api.routes.v1.webhooks import revenuecat_webhook
from src.app.services.streak_restore_ledger import PackPurchase, credit_pack
from src.domain.model.streak import (
    LedgerEntry,
    LedgerKind,
    LedgerSummary,
    StreakRestorePolicy,
)
from src.domain.ports.streak_restore_repository_port import (
    StreakRestoreRepositoryPort,
)

pytestmark = pytest.mark.asyncio

ROUTE = "src.api.routes.v1.webhooks"
LIFECYCLE = "src.api.routes.v1.webhook_subscription_lifecycle"
USER_A, USER_B = SimpleNamespace(id="user-a"), SimpleNamespace(id="user-b")
PACK_3 = "nutree_streak_restore_3"


class _Ledger(StreakRestoreRepositoryPort):
    """Keeps the table's rule of one row per transaction and kind."""

    def __init__(self) -> None:
        self.rows: list[LedgerEntry] = []
        self.log: list[str] = []

    async def lock_user(self, user_id):
        self.log.append(f"lock:{user_id}")

    async def summary(self, user_id):
        return LedgerSummary(await self.balance(user_id), False, frozenset())

    async def balance(self, user_id):
        return sum(row.delta for row in self.rows if row.user_id == user_id)

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
        if entry.store_transaction_id and await self.find_transaction(
            entry.store_transaction_id, entry.kind
        ):
            return False
        self.rows.append(entry)
        self.log.append(f"{entry.kind.value}:{entry.user_id}")
        return True

    def kinds(self, user_id):
        return sorted(row.kind.value for row in self.rows if row.user_id == user_id)


def _event(event_type="NON_RENEWING_PURCHASE", product=PACK_3, **fields):
    return {
        "type": event_type,
        "app_user_id": "firebase-uid-a",
        "product_id": product,
        "transaction_id": "tx-1",
        "store": "APP_STORE",
        "environment": "PRODUCTION",
        **fields,
    }


async def _handle(ledger, user, event, policy=None):
    uow = SimpleNamespace(streak_restores=ledger)
    return await handle_streak_restore_event(
        uow, user, event, policy or StreakRestorePolicy()
    )


async def _claim(ledger, user_id, count=3):
    purchase = PackPurchase(f"nutree_streak_restore_{count}", "tx-1", count)
    await credit_pack(SimpleNamespace(streak_restores=ledger), user_id, purchase)


def _uow(ledger=None):
    uow = MagicMock(streak_restores=ledger)
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(
        side_effect=lambda *exc: ledger.log.append("commit") if ledger else None
    )
    return uow


@contextmanager
def _webhook(ledger, user):
    """The route with a fake ledger; subscription side effects are spies."""
    spies = SimpleNamespace(
        affiliate=MagicMock(), publisher=MagicMock(publish=AsyncMock())
    )
    with ExitStack() as stack:

        def mock(target, **kwargs):
            return stack.enter_context(patch(target, **kwargs))

        mock(f"{ROUTE}.os.getenv", return_value="test_secret")
        mock(f"{ROUTE}.AsyncUnitOfWork", side_effect=[_uow(), _uow(ledger)])
        mock(f"{ROUTE}.find_user_for_revenuecat_event", return_value=user)
        mock(f"{ROUTE}._get_affiliate_handler", return_value=spies.affiliate)
        mock(f"{ROUTE}._get_event_publisher", return_value=spies.publisher)
        mock(f"{ROUTE}.get_streak_restore_policy", return_value=StreakRestorePolicy())
        mock(f"{ROUTE}.get_cache_service", return_value=None)
        spies.purge = mock(
            f"{ROUTE}.purge_streak_cache",
            side_effect=lambda cache, user_id: ledger.log.append(f"purge:{user_id}"),
        )
        # The real cancellation path, so a leak would reach the spies below.
        spies.cancel = mock(
            f"{ROUTE}.handle_cancellation", side_effect=handle_cancellation
        )
        spies.transfer = mock(f"{ROUTE}.handle_transfer")
        spies.sync = mock(f"{ROUTE}.sync_redeemed_target_subscription")
        spies.subscription = mock(f"{LIFECYCLE}.get_or_create_subscription")
        spies.referral = mock(f"{LIFECYCLE}.revoke_referral_on_refund")
        yield spies


async def _post(event):
    request = MagicMock(json=AsyncMock(return_value={"event": event}))
    return await revenuecat_webhook(request, authorization="test_secret")


def _assert_subscription_path_untouched(spies):
    spies.cancel.assert_not_called()
    spies.subscription.assert_not_called()
    spies.referral.assert_not_called()
    assert spies.affiliate.mock_calls == []
    assert spies.publisher.mock_calls == []


@pytest.mark.parametrize("product", [PACK_3, f"{PACK_3}:buy"])
async def test_a_pack_purchase_credits_its_restores(product):
    ledger = _Ledger()
    result = await _handle(ledger, USER_A, _event(product=product))

    assert result.owner_id == "user-a"
    assert await ledger.balance("user-a") == 3
    assert ledger.log == ["lock:user-a", "purchase:user-a"]
    (row,) = ledger.rows
    assert (row.store, row.environment) == ("app_store", "production")


async def test_a_redelivered_purchase_credits_nothing():
    ledger = _Ledger()
    for _ in range(2):
        await _handle(ledger, USER_A, _event())
    assert ledger.kinds("user-a") == ["purchase"]
    assert await ledger.balance("user-a") == 3


@pytest.mark.parametrize("store", ["APP_STORE", None])
async def test_the_webhook_after_the_users_own_claim_credits_nothing(store):
    ledger = _Ledger()
    await _claim(ledger, "user-a")
    result = await _handle(ledger, USER_A, _event(store=store))

    assert result.owner_id == "user-a"
    assert ledger.kinds("user-a") == ["purchase"]
    assert await ledger.balance("user-a") == 3


async def test_a_pack_without_a_transaction_id_is_handled_without_a_write(caplog):
    ledger = _Ledger()
    result = await _handle(ledger, USER_A, _event(transaction_id=None))
    assert result is not None and result.owner_id is None
    assert ledger.log == []
    assert "streak_restore_webhook_missing_transaction" in caplog.text


async def test_a_refund_with_no_prior_credit_writes_credit_and_refund_netting_zero():
    ledger = _Ledger()
    await _handle(ledger, USER_A, _event("CANCELLATION"))
    assert ledger.kinds("user-a") == ["purchase", "refund"]
    assert await ledger.balance("user-a") == 0


async def test_a_refund_naming_another_user_debits_the_pack_owner(caplog):
    ledger = _Ledger()
    await _claim(ledger, "user-a")
    result = await _handle(ledger, USER_B, _event("CANCELLATION"))

    assert result.owner_id == "user-a"
    assert await ledger.balance("user-a") == 0
    assert ledger.kinds("user-b") == []
    assert "streak_restore_refund_owner_mismatch" in caplog.text


@pytest.mark.parametrize(
    ("allow_list", "rows"), [(frozenset(), 0), (frozenset({"user-a"}), 1)]
)
async def test_a_production_sandbox_purchase_credits_only_allow_listed_users(
    allow_list, rows
):
    ledger = _Ledger()
    policy = StreakRestorePolicy(production=True, sandbox_user_ids=allow_list)
    await _handle(ledger, USER_A, _event(environment="SANDBOX"), policy)

    assert len(ledger.rows) == rows
    assert all(row.environment == "sandbox" for row in ledger.rows)


async def test_a_reversed_pack_refund_is_logged_and_ignored(caplog):
    ledger = _Ledger()
    caplog.set_level("INFO")
    result = await _handle(ledger, USER_A, _event("REFUND_REVERSED"))
    assert result is not None and result.owner_id is None
    assert ledger.log == []
    assert "streak_restore_webhook_ignored type=REFUND_REVERSED" in caplog.text


async def test_a_pack_refund_after_a_spend_floors_at_zero_off_the_subscription_path():
    ledger = _Ledger()
    await _claim(ledger, "user-a")
    for day in (26, 27):
        spend = LedgerEntry(
            "user-a", LedgerKind.SPEND, -1, restored_date=date(2026, 10, day)
        )
        await ledger.insert_entry(spend)
    with _webhook(ledger, USER_A) as spies:
        assert await _post(_event("CANCELLATION")) == {"status": "success"}
    # Three refunded, one left to take back.
    assert [row.delta for row in ledger.rows if row.kind is LedgerKind.REFUND] == [-1]
    assert await ledger.balance("user-a") == 0
    _assert_subscription_path_untouched(spies)


async def test_an_uncountable_pack_refund_is_acknowledged_with_an_error(caplog):
    ledger = _Ledger()
    with _webhook(ledger, USER_A) as spies:
        result = await _post(_event("CANCELLATION", product="nutree_streak_restore_x"))
    assert result == {"status": "success"}
    assert "streak_restore_webhook_unknown_product" in caplog.text
    assert ledger.log == ["commit"]
    spies.purge.assert_not_called()
    _assert_subscription_path_untouched(spies)


async def test_the_streak_cache_is_purged_after_the_ledger_commits():
    ledger = _Ledger()
    with _webhook(ledger, USER_A):
        assert await _post(_event()) == {"status": "success"}
    assert ledger.log == ["lock:user-a", "purchase:user-a", "commit", "purge:user-a"]


async def test_a_pack_purchase_for_an_unknown_user_returns_404_for_a_retry():
    ledger = _Ledger()
    with _webhook(ledger, None), pytest.raises(HTTPException) as error:
        await _post(_event())
    assert error.value.status_code == 404
    assert ledger.rows == []


async def test_a_subscription_cancellation_still_reaches_the_subscription_handler():
    ledger = _Ledger()
    with _webhook(ledger, USER_A) as spies:
        await _post(_event("CANCELLATION", product="nutree_2999_1y_0"))
    spies.cancel.assert_awaited_once()
    spies.subscription.assert_awaited_once()
    spies.publisher.publish.assert_awaited_once()
    spies.purge.assert_not_called()
    assert ledger.log == ["commit"]


async def test_a_transfer_keeps_its_own_path_and_moves_no_ledger_rows():
    ledger = _Ledger()
    await _claim(ledger, "user-a")
    transfer = {"type": "TRANSFER", "transferred_from": ["a"], "transferred_to": ["b"]}
    with _webhook(ledger, USER_B) as spies:
        assert await _post(transfer) == {"status": "success"}
    spies.transfer.assert_awaited_once()
    spies.sync.assert_awaited_once()
    assert [row.user_id for row in ledger.rows] == ["user-a"]

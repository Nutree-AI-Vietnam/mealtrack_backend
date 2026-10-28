"""Restoring streak days: free, held and just-bought restores."""

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from tests.fixtures.fakes.fake_streak_restore_ledger import InMemoryLedger

from src.api.exceptions import (
    AuthorizationException,
    BusinessLogicException,
    ConflictException,
    ExternalServiceException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.streak import PackClaim, RestoreStreakCommand
from src.app.handlers.command_handlers.restore_streak_command_handler import (
    RestoreStreakCommandHandler,
)
from src.domain.model.streak import LedgerEntry, LedgerKind, StreakRestorePolicy
from src.domain.ports.subscription_service_port import (
    OneTimePurchase,
    OneTimePurchaseLookup,
)

ZONE = "Asia/Ho_Chi_Minh"
USER = "user-1"
TODAY = datetime.now(ZoneInfo(ZONE)).date()
YESTERDAY = TODAY - timedelta(days=1)
FOUND = OneTimePurchase(OneTimePurchaseLookup.FOUND, store="app_store")


class _World:
    """One user's logs and ledger; a unit of work rolls the ledger back on error."""

    def __init__(self, last_logged: date = YESTERDAY - timedelta(days=1)):
        self.ledger = InMemoryLedger()
        self.logs = [(last_logged - timedelta(days=n), True) for n in range(12)]
        self.events: list[str] = []
        self.commits = 0

    def uow(self):
        world = self

        class _Uow:
            async def __aenter__(self):
                self._rows = list(world.ledger.rows)
                return SimpleNamespace(
                    streak_restores=world.ledger,
                    users=SimpleNamespace(
                        get_user_timezone=AsyncMock(return_value=ZONE),
                        find_by_id=AsyncMock(
                            return_value=SimpleNamespace(firebase_uid="fb-1")
                        ),
                    ),
                    meals=SimpleNamespace(
                        get_streak_log_days=AsyncMock(return_value=world.logs),
                        count_by_source=AsyncMock(return_value=0),
                    ),
                )

            async def __aexit__(self, exc_type, exc, tb):
                if exc_type:
                    world.ledger.rows[:] = self._rows
                else:
                    world.commits += 1
                    world.events.append("commit")
                return False

        return _Uow()

    def seed(self, kind, delta, **fields):
        self.ledger.rows.append(LedgerEntry(USER, kind, delta, **fields))

    def kinds(self):
        return sorted(row.kind.value for row in self.ledger.rows)


def _handler(world, lookups=(FOUND,), **policy):
    cache = MagicMock()
    cache.purge = AsyncMock(side_effect=lambda *a: world.events.append("purge"))
    subscriptions = MagicMock()
    subscriptions.find_one_time_purchase = AsyncMock(side_effect=list(lookups))
    handler = RestoreStreakCommandHandler(
        uow_factory=world.uow,
        cache_service=cache,
        subscription_service=subscriptions,
        policy=StreakRestorePolicy(**policy),
        retry_delay_seconds=0,
    )
    return handler, subscriptions


def _cmd(dates=(YESTERDAY,), product="nutree_streak_restore_3", transaction="tx-1"):
    purchase = PackClaim(product, transaction) if product else None
    return RestoreStreakCommand(user_id=USER, dates=list(dates), purchase=purchase)


def _free(dates=(YESTERDAY,)):
    return _cmd(dates, product=None)


@pytest.mark.asyncio
async def test_the_free_restore_fills_one_day():
    world = _World()
    handler, _ = _handler(world)
    result = await handler.handle(_free())
    assert result["outcome"] == "restored"
    assert result["restored_dates"] == [YESTERDAY.isoformat()]
    assert result["credited"] == 0
    assert result["streak"]["current_streak"] == 12
    assert result["streak"]["restore"]["free_available"] is False
    assert world.kinds() == ["free"]
    assert world.events == ["commit", "purge"]


@pytest.mark.asyncio
async def test_two_days_use_the_free_restore_and_a_held_one():
    world = _World(last_logged=TODAY - timedelta(days=3))
    world.seed(LedgerKind.PURCHASE, 1, store_transaction_id="old")
    handler, _ = _handler(world)
    days = (TODAY - timedelta(days=2), YESTERDAY)
    result = await handler.handle(_free(days))
    assert result["outcome"] == "restored"
    assert world.kinds() == ["free", "purchase", "spend"]
    assert result["streak"]["restore"]["balance"] == 0


@pytest.mark.asyncio
async def test_buying_a_pack_credits_and_spends_in_one_commit():
    world = _World()
    world.seed(LedgerKind.FREE, 0, restored_date=TODAY - timedelta(days=40))
    handler, _ = _handler(world)
    result = await handler.handle(_cmd())
    assert (result["outcome"], result["credited"]) == ("restored", 3)
    assert result["streak"]["restore"]["balance"] == 2
    # One read-only lookup of the user, then one write.
    assert world.commits == 2
    assert world.events[-1] == "purge"


@pytest.mark.asyncio
async def test_a_pack_with_no_days_is_only_credited():
    world = _World()
    handler, _ = _handler(world)
    result = await handler.handle(_cmd(dates=()))
    assert (result["outcome"], result["credited"]) == ("credited_only", 3)
    assert world.kinds() == ["purchase"]


@pytest.mark.asyncio
async def test_days_already_restored_return_restored_and_write_nothing():
    world = _World()
    world.seed(LedgerKind.FREE, 0, restored_date=YESTERDAY)
    handler, _ = _handler(world)
    result = await handler.handle(_free())
    assert result["outcome"] == "restored"
    assert world.kinds() == ["free"]


@pytest.mark.asyncio
async def test_other_days_are_refused_without_a_purchase():
    world = _World()
    handler, _ = _handler(world)
    with pytest.raises(ConflictException) as error:
        await handler.handle(_free((TODAY - timedelta(days=5),)))
    assert error.value.error_code == "STREAK_NOT_RESTORABLE"
    assert error.value.details == {"missed_dates": [YESTERDAY.isoformat()]}


@pytest.mark.asyncio
async def test_other_days_with_a_purchase_still_credit_the_pack():
    world = _World()
    handler, _ = _handler(world)
    result = await handler.handle(_cmd(dates=(TODAY - timedelta(days=5),)))
    assert result["outcome"] == "credited_only"
    assert world.kinds() == ["purchase"]


@pytest.mark.asyncio
async def test_no_restores_left_is_refused():
    world = _World()
    world.seed(LedgerKind.FREE, 0, restored_date=TODAY - timedelta(days=40))
    handler, _ = _handler(world)
    with pytest.raises(ConflictException) as error:
        await handler.handle(_free())
    assert error.value.error_code == "NO_RESTORES_LEFT"
    assert error.value.details == {"needed": 1, "available": 0}
    assert "purge" not in world.events


@pytest.mark.asyncio
async def test_someone_elses_transaction_is_refused_and_credits_nothing():
    world = _World()
    world.ledger.rows.append(
        LedgerEntry("other", LedgerKind.PURCHASE, 3, store_transaction_id="tx-1")
    )
    handler, subscriptions = _handler(world)
    with pytest.raises(ConflictException) as error:
        await handler.handle(_cmd())
    assert error.value.error_code == "PURCHASE_OWNED_BY_ANOTHER_ACCOUNT"
    subscriptions.find_one_time_purchase.assert_not_awaited()
    assert world.kinds() == ["purchase"]
    assert await world.ledger.balance(USER) == 0


@pytest.mark.asyncio
async def test_production_refuses_sandbox_purchases_of_unlisted_users():
    world = _World()
    sandbox = OneTimePurchase(OneTimePurchaseLookup.FOUND, is_sandbox=True)
    handler, _ = _handler(world, lookups=[sandbox], production=True)
    with pytest.raises(AuthorizationException) as error:
        await handler.handle(_cmd())
    assert error.value.error_code == "SANDBOX_PURCHASE_NOT_ALLOWED"
    assert world.ledger.rows == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy", [{"production": True, "sandbox_user_ids": frozenset({USER})}, {}]
)
async def test_sandbox_purchases_credit_listed_users_and_everyone_off_production(
    policy,
):
    world = _World()
    sandbox = OneTimePurchase(OneTimePurchaseLookup.FOUND, is_sandbox=True)
    handler, _ = _handler(world, lookups=[sandbox], **policy)
    await handler.handle(_cmd(dates=()))
    assert world.ledger.rows[0].environment == "sandbox"


@pytest.mark.asyncio
async def test_claims_switched_off_answer_503_and_write_nothing():
    world = _World()
    handler, subscriptions = _handler(world, claim_enabled=False)
    with pytest.raises(ExternalServiceException) as error:
        await handler.handle(_cmd())
    assert error.value.error_code == "FEATURE_DISABLED"
    subscriptions.find_one_time_purchase.assert_not_awaited()
    assert world.ledger.rows == []


@pytest.mark.asyncio
async def test_spending_switched_off_refuses_free_restores():
    world = _World()
    handler, _ = _handler(world, spend_enabled=False)
    with pytest.raises(ExternalServiceException) as error:
        await handler.handle(_free())
    assert error.value.error_code == "FEATURE_DISABLED"


@pytest.mark.asyncio
async def test_spending_switched_off_still_credits_a_purchase():
    world = _World()
    handler, _ = _handler(world, spend_enabled=False)
    result = await handler.handle(_cmd())
    assert result["outcome"] == "credited_only"
    assert world.kinds() == ["purchase"]


@pytest.mark.asyncio
async def test_a_missing_purchase_is_looked_up_once_more_then_404():
    world = _World()
    missing = OneTimePurchase(OneTimePurchaseLookup.NOT_FOUND)
    handler, subscriptions = _handler(world, lookups=[missing, missing])
    with pytest.raises(ResourceNotFoundException) as error:
        await handler.handle(_cmd())
    assert error.value.error_code == "PURCHASE_NOT_FOUND"
    assert subscriptions.find_one_time_purchase.await_count == 2


@pytest.mark.asyncio
async def test_a_purchase_found_on_the_second_lookup_is_credited():
    world = _World()
    missing = OneTimePurchase(OneTimePurchaseLookup.NOT_FOUND)
    handler, _ = _handler(world, lookups=[missing, FOUND])
    result = await handler.handle(_cmd(dates=()))
    assert result["credited"] == 3


@pytest.mark.asyncio
async def test_revenuecat_down_answers_503_and_writes_nothing():
    world = _World()
    down = OneTimePurchase(OneTimePurchaseLookup.UNAVAILABLE)
    handler, subscriptions = _handler(world, lookups=[down])
    with pytest.raises(ExternalServiceException) as error:
        await handler.handle(_cmd())
    assert error.value.error_code == "PURCHASE_VERIFICATION_UNAVAILABLE"
    assert subscriptions.find_one_time_purchase.await_count == 1
    assert world.ledger.rows == []


@pytest.mark.asyncio
async def test_an_unknown_product_is_refused_before_any_lookup():
    world = _World()
    handler, subscriptions = _handler(world)
    with pytest.raises(BusinessLogicException) as error:
        await handler.handle(_cmd(product="nutree_premium_monthly"))
    assert error.value.error_code == "UNKNOWN_RESTORE_PRODUCT"
    subscriptions.find_one_time_purchase.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        _free(
            (YESTERDAY - timedelta(days=2), YESTERDAY - timedelta(days=1), YESTERDAY)
        ),
        _free((YESTERDAY, YESTERDAY - timedelta(days=1))),
        _free((YESTERDAY, YESTERDAY)),
        _free(()),
    ],
)
async def test_bad_days_are_refused(command):
    handler, _ = _handler(_World())
    with pytest.raises(ValidationException):
        await handler.handle(command)


@pytest.mark.asyncio
async def test_a_pack_already_on_the_ledger_is_not_looked_up_again():
    # The webhook credited it; RevenueCat may no longer list it.
    world = _World()
    world.seed(LedgerKind.FREE, 0, restored_date=TODAY - timedelta(days=40))
    world.seed(
        LedgerKind.PURCHASE,
        3,
        product_id="nutree_streak_restore_3",
        store_transaction_id="tx-1",
    )
    missing = OneTimePurchase(OneTimePurchaseLookup.NOT_FOUND)
    handler, subscriptions = _handler(world, lookups=[missing, missing])
    result = await handler.handle(_cmd())
    assert (result["outcome"], result["credited"]) == ("restored", 3)
    subscriptions.find_one_time_purchase.assert_not_awaited()
    assert world.kinds() == ["free", "purchase", "spend"]


@pytest.mark.asyncio
async def test_a_refunded_pack_credits_nothing():
    world = _World()
    world.seed(LedgerKind.PURCHASE, 3, store_transaction_id="tx-1")
    world.seed(LedgerKind.REFUND, -3, store_transaction_id="tx-1")
    handler, _ = _handler(world)
    result = await handler.handle(_cmd(dates=()))
    assert (result["outcome"], result["credited"]) == ("credited_only", 0)


@pytest.mark.asyncio
async def test_a_hanging_lookup_answers_503(monkeypatch):
    monkeypatch.setattr(
        "src.app.services.streak_restore_purchase.LOOKUP_TIMEOUT_SECONDS", 0.01
    )
    world = _World()
    handler, subscriptions = _handler(world)

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    subscriptions.find_one_time_purchase = hang
    with pytest.raises(ExternalServiceException) as error:
        await handler.handle(_cmd())
    assert error.value.error_code == "PURCHASE_VERIFICATION_UNAVAILABLE"
    assert world.ledger.rows == []


@pytest.mark.asyncio
async def test_paused_claims_still_answer_for_a_pack_the_ledger_holds():
    world = _World()
    world.seed(
        LedgerKind.PURCHASE,
        3,
        product_id="nutree_streak_restore_3",
        store_transaction_id="tx-1",
    )
    handler, subscriptions = _handler(world, claim_enabled=False)
    result = await handler.handle(_cmd(dates=()))
    assert (result["outcome"], result["credited"]) == ("credited_only", 3)
    subscriptions.find_one_time_purchase.assert_not_awaited()

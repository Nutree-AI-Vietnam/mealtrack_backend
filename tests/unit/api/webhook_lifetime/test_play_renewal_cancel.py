"""A Play lifetime purchase turns off the buyer's Play plan renewals, after commit."""

import asyncio
import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
    USER,
    commit,
    entitled,
    event,
    lifetime,
    rows,
    yearly,
)
from tests.fixtures.fakes.play_renewal_fakes import (
    MONTHLY_ORDER,
    PLAY_MONTHLY,
    PLAY_YEARLY,
    YEARLY_ORDER,
    add_plan_row,
    play_lifetime,
    play_yearly_purchase,
)

from src.api.routes.v1.webhook_lifetime_purchase import handle_lifetime_purchase
from src.api.routes.v1.webhook_play_renewal_cancel import cancel_play_renewals
from src.api.routes.v1.webhooks import handle_cancellation, handle_purchase
from src.domain.ports.play_subscription_port import PlayBillingUnavailableError
from src.domain.utils.timezone_utils import utc_now

pytestmark = pytest.mark.asyncio

UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID
PLAY_CANCEL = "src.api.routes.v1.webhook_play_renewal_cancel"


@pytest.fixture
def buy(uow, publisher):
    """One lifetime webhook: its unit of work commits, then the cancel runs."""

    async def run(payload):
        renewals = await handle_lifetime_purchase(
            uow, USER, payload, event_publisher=publisher
        )
        commit(uow)
        await cancel_play_renewals(payload, renewals)
        return renewals

    return run


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


# --- What is cancelled ---


async def test_a_play_upgrade_cancels_the_yearly_renewal_it_replaces(
    uow, deliver, buy, play
):
    await deliver(handle_purchase, play_yearly_purchase())

    renewals = await buy(play_lifetime(UPGRADE))

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER)
    assert [r.product_id for r in renewals] == [PLAY_YEARLY]
    (row,) = await rows(uow)  # the yearly row became the lifetime row
    assert (row.product_id, row.expires_at) == (UPGRADE, None)
    assert await entitled(uow)


async def test_lifetime_bought_outright_cancels_every_renewing_play_plan(
    uow, buy, play
):
    add_plan_row(uow, PLAY_YEARLY, YEARLY_ORDER)
    add_plan_row(uow, PLAY_MONTHLY, MONTHLY_ORDER, status="billing_issue")

    await buy(play_lifetime())

    cancelled = {call.args[0] for call in play.cancel_renewal.await_args_list}
    assert cancelled == {YEARLY_ORDER, MONTHLY_ORDER}
    assert await entitled(uow)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "cancelled"},
        {"status": "expired"},
        {"status": "refunded"},
        {"expires_at": utc_now() - timedelta(days=1)},
        {"user_id": "user-2"},  # another Nutree account on the store account
        {"platform": "ios"},
        {"is_sandbox": True},  # a sandbox plan, the purchase is production
        {"product_id": LIFETIME_PRODUCT_ID, "expires_at": None},
    ],
    ids=[
        "cancelled",
        "expired",
        "refunded",
        "period-over",
        "other-account",
        "app-store",
        "other-environment",
        "lifetime",
    ],
)
async def test_a_plan_that_no_longer_renews_here_is_left_alone(uow, buy, play, fields):
    add_plan_row(uow, **fields)

    renewals = await buy(play_lifetime(transaction_id="GPA.3372-0000-0000-00001"))

    assert renewals == []
    play.getter.assert_not_called()
    play.cancel_renewal.assert_not_awaited()


async def test_a_yearly_already_turned_off_is_not_cancelled_again(
    uow, deliver, buy, play
):
    await deliver(handle_purchase, play_yearly_purchase())
    await deliver(
        handle_cancellation, event("CANCELLATION", PLAY_YEARLY, store="PLAY_STORE")
    )

    await buy(play_lifetime(UPGRADE))

    play.cancel_renewal.assert_not_awaited()
    assert await entitled(uow)


async def test_without_a_play_plan_nothing_is_cancelled(uow, buy, play):
    assert await buy(play_lifetime()) == []

    play.getter.assert_not_called()
    assert await entitled(uow)


@pytest.mark.parametrize(
    "plan", ["app-store-plan", "play-plan"], ids=["app-store", "play-plan"]
)
async def test_an_app_store_purchase_never_cancels_anything(
    uow, deliver, buy, play, plan
):
    if plan == "play-plan":
        add_plan_row(uow)
    else:
        await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))

    assert await buy(lifetime(UPGRADE)) == []

    play.getter.assert_not_called()
    (row, *_) = [r for r in await rows(uow) if r.product_id == UPGRADE]
    assert row.platform == "ios" and await entitled(uow)


# --- Once, and never in the way of the grant ---


async def test_a_redelivered_upgrade_cancels_nothing_again(uow, deliver, buy, play):
    await deliver(handle_purchase, play_yearly_purchase())
    upgrade = play_lifetime(UPGRADE)

    await buy(upgrade)
    assert await buy(upgrade) == []

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER)


async def test_a_redelivered_outright_purchase_cancels_nothing_again(uow, buy, play):
    add_plan_row(uow)

    await buy(play_lifetime())
    await buy(play_lifetime())

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER)
    assert len(await rows(uow)) == 2


async def test_with_the_adapter_off_nothing_is_cancelled(uow, buy, play, caplog):
    play.enabled = False
    add_plan_row(uow)

    await buy(play_lifetime())

    play.cancel_renewal.assert_not_awaited()
    assert "Play renewal cancel is off" in caplog.text
    assert await entitled(uow)


@pytest.mark.parametrize("sandbox_enabled", [False, True])
async def test_a_sandbox_purchase_cancels_only_when_sandbox_is_allowed(
    uow, buy, play, sandbox_enabled
):
    play.sandbox_enabled = sandbox_enabled
    add_plan_row(uow, is_sandbox=True)

    await buy(play_lifetime(environment="SANDBOX"))

    assert play.cancel_renewal.await_count == (1 if sandbox_enabled else 0)
    assert await entitled(uow, exclude_sandbox=False)


async def test_a_failed_cancel_is_logged_and_the_next_plan_still_tried(
    uow, buy, play, caplog
):
    add_plan_row(uow, PLAY_YEARLY, YEARLY_ORDER)
    add_plan_row(uow, PLAY_MONTHLY, MONTHLY_ORDER)
    play.cancel_renewal.side_effect = [
        PlayBillingUnavailableError(
            "Play subscriptionsv2.cancel returned 503", status_code=503
        ),
        RuntimeError("unexpected"),
    ]

    await buy(play_lifetime())

    assert play.cancel_renewal.await_count == 2
    errors = _errors(caplog)
    assert any("PlayBillingUnavailableError" in e and "503" in e for e in errors)
    assert any("failed unexpectedly" in e for e in errors)
    assert not any(order in caplog.text for order in (YEARLY_ORDER, MONTHLY_ORDER))
    assert await entitled(uow)


async def test_a_plan_without_an_order_id_is_reported(uow, buy, play, caplog):
    add_plan_row(uow, order=None)

    await buy(play_lifetime())

    play.cancel_renewal.assert_not_awaited()
    assert any("has no order id" in e for e in _errors(caplog))


async def test_a_slow_play_stops_at_the_budget(uow, buy, play, caplog):
    add_plan_row(uow)

    async def hang(order_id):
        await asyncio.sleep(5)

    play.cancel_renewal.side_effect = hang
    with patch(f"{PLAY_CANCEL}.CANCEL_BUDGET_SECONDS", 0.05):
        await asyncio.wait_for(buy(play_lifetime()), timeout=2)

    assert any("ran out of time" in e for e in _errors(caplog))
    assert await entitled(uow)


async def test_an_adapter_that_cannot_be_built_is_logged(uow, buy, play, caplog):
    add_plan_row(uow)
    play.getter.side_effect = RuntimeError("settings broke")

    await buy(play_lifetime())

    assert any("could not start" in e for e in _errors(caplog))
    assert await entitled(uow)


async def test_cancel_play_renewals_without_renewals_does_nothing(play):
    await cancel_play_renewals(play_lifetime(), [])

    play.getter.assert_not_called()

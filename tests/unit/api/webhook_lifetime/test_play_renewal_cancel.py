"""Which Play plans a Play lifetime purchase asks Google to stop, after commit."""

from datetime import timedelta

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
from src.domain.utils.timezone_utils import utc_now

pytestmark = pytest.mark.asyncio

UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID


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


def _asked(play) -> set[tuple[str, str]]:
    return {tuple(call.args) for call in play.cancel_renewal.await_args_list}


def _days_ago(days: int):
    return utc_now() - timedelta(days=days)


# --- What is asked about ---


async def test_a_play_upgrade_cancels_the_yearly_renewal_it_replaces(
    uow, deliver, buy, play
):
    await deliver(handle_purchase, play_yearly_purchase())

    renewals = await buy(play_lifetime(UPGRADE))

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER, PLAY_YEARLY)
    assert [r.product_id for r in renewals] == [PLAY_YEARLY]
    (row,) = await rows(uow)  # the yearly row became the lifetime row
    assert (row.product_id, row.expires_at) == (UPGRADE, None)
    assert await entitled(uow)


async def test_lifetime_bought_outright_stops_every_play_plan_not_only_the_yearly(
    uow, buy, play
):
    """One entitlement covers every plan, so a monthly beside lifetime goes too."""
    add_plan_row(uow, PLAY_YEARLY, YEARLY_ORDER)
    add_plan_row(uow, PLAY_MONTHLY, MONTHLY_ORDER, status="billing_issue")

    await buy(play_lifetime())

    assert _asked(play) == {(YEARLY_ORDER, PLAY_YEARLY), (MONTHLY_ORDER, PLAY_MONTHLY)}
    assert await entitled(uow)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "cancelled", "expires_at": _days_ago(3)},
        {"status": "billing_issue", "expires_at": _days_ago(10)},
        {"status": "expired", "expires_at": _days_ago(60)},
        {"status": "cancelled"},  # turned off, then maybe restored in Play
        {"expires_at": None},
    ],
    ids=[
        "billing-error-cancellation",
        "on-hold-past-expiry",
        "expired-within-lookback",
        "cancelled-in-period",
        "no-expiry",
    ],
)
async def test_a_plan_our_rows_think_is_over_is_still_checked_with_google(
    uow, buy, play, fields
):
    """Play retries a failed charge for weeks; only its own state is reliable."""
    add_plan_row(uow, **fields)

    await buy(play_lifetime())

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER, PLAY_YEARLY)


async def test_a_yearly_cancelled_after_a_failed_charge_is_still_checked(
    uow, deliver, buy, play
):
    await deliver(handle_purchase, play_yearly_purchase())
    cancellation = event(
        "CANCELLATION",
        PLAY_YEARLY,
        store="PLAY_STORE",
        cancel_reason="BILLING_ERROR",
    )
    await deliver(handle_cancellation, cancellation)

    await buy(play_lifetime(UPGRADE))

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER, PLAY_YEARLY)
    assert await entitled(uow)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "refunded"},
        {"status": "expired", "expires_at": _days_ago(91)},
        {"user_id": "user-2"},  # another Nutree account on the store account
        {"platform": "ios"},
        {"is_sandbox": True},  # a sandbox plan, the purchase is production
        {"product_id": LIFETIME_PRODUCT_ID, "expires_at": None},
    ],
    ids=[
        "refunded",
        "ended-long-ago",
        "other-account",
        "app-store",
        "other-environment",
        "lifetime",
    ],
)
async def test_a_plan_that_cannot_charge_this_buyer_is_left_alone(
    uow, buy, play, fields
):
    add_plan_row(uow, **fields)

    renewals = await buy(play_lifetime(transaction_id="GPA.3372-0000-0000-00001"))

    assert renewals == []
    play.getter.assert_not_called()


async def test_two_rows_of_one_subscription_are_asked_about_once(uow, buy, play):
    add_plan_row(uow, order=YEARLY_ORDER)
    add_plan_row(uow, order=f"{YEARLY_ORDER}..1", status="cancelled")

    await buy(play_lifetime())

    assert play.cancel_renewal.await_count == 1


async def test_without_a_play_plan_nothing_is_asked(uow, buy, play):
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


# --- Once ---


async def test_a_redelivered_upgrade_asks_nothing_again(uow, deliver, buy, play):
    await deliver(handle_purchase, play_yearly_purchase())
    upgrade = play_lifetime(UPGRADE)

    await buy(upgrade)
    assert await buy(upgrade) == []

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER, PLAY_YEARLY)


async def test_a_redelivered_outright_purchase_asks_nothing_again(uow, buy, play):
    add_plan_row(uow)

    await buy(play_lifetime())
    await buy(play_lifetime())

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER, PLAY_YEARLY)
    assert len(await rows(uow)) == 2

"""A Play plan bought or renewed while its buyer holds a Play lifetime is cancelled."""

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
    USER,
    commit,
    entitled,
    lifecycle_types,
    lifetime,
    ms,
    rows,
    yearly,
)
from tests.fixtures.fakes.play_renewal_fakes import (
    LIFETIME_ORDER,
    PLAY_YEARLY,
    YEARLY_ORDER,
    add_plan_row,
    play_lifetime,
    play_yearly_purchase,
    post_webhook,
    webhook_route,
)

from src.api.routes.v1.webhook_lifetime_purchase import handle_lifetime_purchase
from src.api.routes.v1.webhook_play_renewal_cancel import play_plan_beside_lifetime
from src.domain.ports.play_subscription_port import PlayBillingUnavailableError

pytestmark = pytest.mark.asyncio

UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID
RENEWAL_ORDER = f"{YEARLY_ORDER}..0"


def _play_lifetime_row(uow, **fields):
    fields.setdefault("expires_at", None)
    return add_plan_row(uow, LIFETIME_PRODUCT_ID, LIFETIME_ORDER, **fields)


def _play_renewal(**fields):
    return play_yearly_purchase(
        type="RENEWAL", id="evt-renewal", transaction_id=RENEWAL_ORDER, **fields
    )


# --- Cancelled ---


async def test_a_play_yearly_arriving_after_the_play_upgrade_is_cancelled(
    uow, publisher, play
):
    """The upgrade's webhook came first, so the yearly had no row to pick."""
    assert (
        await handle_lifetime_purchase(
            uow, USER, play_lifetime(UPGRADE), event_publisher=publisher
        )
        == []
    )
    commit(uow)
    seen: list = []

    async def cancel(order_id):
        seen.append(("cancel", order_id, uow.session.sync.in_transaction()))

    play.cancel_renewal.side_effect = cancel
    with webhook_route(uow, publisher, seen):
        assert await post_webhook(play_yearly_purchase()) == {"status": "success"}

    assert seen[-2:] == ["commit", ("cancel", YEARLY_ORDER, False)]
    (row,) = await rows(uow)  # the yearly gets no row beside the lifetime
    assert (row.product_id, row.expires_at) == (UPGRADE, None)
    assert await entitled(uow)


async def test_a_play_renewal_beside_a_play_lifetime_is_cancelled(uow, publisher, play):
    _play_lifetime_row(uow)
    yearly_row = add_plan_row(uow)

    with webhook_route(uow, publisher):
        assert await post_webhook(_play_renewal()) == {"status": "success"}

    play.cancel_renewal.assert_awaited_once_with(RENEWAL_ORDER)
    assert lifecycle_types(publisher) == ["subscription_renewal"]
    (renewal,) = await play_plan_beside_lifetime(uow, USER, _play_renewal())
    assert (renewal.row_id, renewal.product_id) == (yearly_row.id, PLAY_YEARLY)


async def test_a_failing_cancel_still_stores_the_plan_event(uow, publisher, play):
    _play_lifetime_row(uow)
    play.cancel_renewal.side_effect = PlayBillingUnavailableError(
        "down", status_code=503
    )

    with webhook_route(uow, publisher):
        assert await post_webhook(play_yearly_purchase()) == {"status": "success"}

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER)
    assert await entitled(uow)


# --- Left alone ---


@pytest.mark.parametrize("event_type", ["INITIAL_PURCHASE", "RENEWAL"])
async def test_a_play_yearly_without_lifetime_is_not_cancelled(
    uow, publisher, play, event_type
):
    payload = play_yearly_purchase() if event_type == "INITIAL_PURCHASE" else None
    with webhook_route(uow, publisher):
        assert await post_webhook(payload or _play_renewal()) == {"status": "success"}

    play.getter.assert_not_called()
    (row,) = await rows(uow)
    assert (row.product_id, row.platform, row.status) == (
        PLAY_YEARLY,
        "android",
        "active",
    )


async def test_an_app_store_yearly_after_a_play_lifetime_is_not_cancelled(
    uow, publisher, play
):
    _play_lifetime_row(uow)

    with webhook_route(uow, publisher):
        assert await post_webhook(yearly("INITIAL_PURCHASE")) == {"status": "success"}

    play.getter.assert_not_called()


async def test_a_play_yearly_beside_an_app_store_lifetime_is_not_cancelled(
    uow, deliver, publisher, play
):
    await deliver(handle_lifetime_purchase, lifetime())  # bought on the App Store

    with webhook_route(uow, publisher):
        assert await post_webhook(play_yearly_purchase()) == {"status": "success"}

    play.getter.assert_not_called()


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "refunded"},
        {"user_id": "user-2"},
        {"is_sandbox": True},
    ],
    ids=["refunded-lifetime", "other-account", "other-environment"],
)
async def test_a_lifetime_that_is_not_the_buyers_here_cancels_nothing(
    uow, publisher, play, fields
):
    _play_lifetime_row(uow, **fields)

    with webhook_route(uow, publisher):
        assert await post_webhook(play_yearly_purchase()) == {"status": "success"}

    play.getter.assert_not_called()


# --- The same switches ---


async def test_with_the_adapter_off_the_plan_keeps_renewing(uow, publisher, play):
    play.enabled = False
    _play_lifetime_row(uow)

    with webhook_route(uow, publisher):
        assert await post_webhook(play_yearly_purchase()) == {"status": "success"}

    play.cancel_renewal.assert_not_awaited()


@pytest.mark.parametrize("sandbox_enabled", [False, True])
async def test_a_sandbox_plan_is_cancelled_only_when_sandbox_is_allowed(
    uow, publisher, play, sandbox_enabled
):
    play.sandbox_enabled = sandbox_enabled
    _play_lifetime_row(uow, is_sandbox=True)

    with webhook_route(uow, publisher):
        payload = play_yearly_purchase(environment="SANDBOX", expiration_at_ms=ms(1))
        assert await post_webhook(payload) == {"status": "success"}

    assert play.cancel_renewal.await_count == (1 if sandbox_enabled else 0)

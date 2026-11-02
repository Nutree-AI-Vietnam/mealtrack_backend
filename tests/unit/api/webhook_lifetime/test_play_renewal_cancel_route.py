"""Through the RevenueCat route: the grant commits first, then Play is asked."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_UPGRADE_PRODUCT_ID,
    USER,
    entitled,
    lifetime,
    rows,
)
from tests.fixtures.fakes.play_renewal_fakes import (
    PLAY_YEARLY,
    YEARLY_ORDER,
    play_lifetime,
    play_yearly_purchase,
    post_webhook,
    webhook_route,
)

from src.api.routes.v1.webhooks import handle_purchase
from src.domain.ports.play_subscription_port import PlayBillingConfigurationError
from src.infra.adapters.google_play_subscription_adapter import (
    API_ROOT,
    CANCEL_BODY,
    GooglePlaySubscriptionAdapter,
)

pytestmark = pytest.mark.asyncio

PLAY_CANCEL = "src.api.routes.v1.webhook_play_renewal_cancel"
UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID


async def test_the_grant_commits_before_play_is_asked(uow, deliver, publisher, play):
    await deliver(handle_purchase, play_yearly_purchase())
    seen: list = []

    async def cancel(order_id, product_id):
        # Nothing of the purchase is still uncommitted when Play is asked.
        seen.append(("cancel", order_id, uow.session.sync.in_transaction()))

    play.cancel_renewal.side_effect = cancel
    with webhook_route(uow, publisher, seen):
        assert await post_webhook(play_lifetime(UPGRADE)) == {"status": "success"}

    assert seen[-2:] == ["commit", ("cancel", YEARLY_ORDER, False)]


@pytest.mark.parametrize(
    "failure",
    [PlayBillingConfigurationError("403", status_code=403), RuntimeError("boom")],
)
async def test_a_failing_cancel_still_answers_success_with_lifetime_stored(
    uow, deliver, publisher, play, failure
):
    await deliver(handle_purchase, play_yearly_purchase())
    play.cancel_renewal.side_effect = failure

    with webhook_route(uow, publisher, []):
        assert await post_webhook(play_lifetime(UPGRADE)) == {"status": "success"}

    (row,) = await rows(uow)
    assert (row.product_id, row.status, row.expires_at) == (UPGRADE, "active", None)
    assert await entitled(uow)


async def test_a_duplicate_webhook_cancels_once(uow, deliver, publisher, play):
    await deliver(handle_purchase, play_yearly_purchase())

    with webhook_route(uow, publisher, []):
        for _ in range(2):
            assert await post_webhook(play_lifetime(UPGRADE)) == {"status": "success"}

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER, PLAY_YEARLY)
    assert len(await rows(uow)) == 1


PACKAGE = "com.nutreeai.mobile"
PURCHASE_TOKEN = "purchase-token-of-the-yearly"
SUBSCRIPTION_URL = (
    f"{API_ROOT}/{PACKAGE}/purchases/subscriptionsv2/tokens/{PURCHASE_TOKEN}"
)


def _real_adapter(client):
    return GooglePlaySubscriptionAdapter(
        package_name=PACKAGE,
        access_token=MagicMock(get=AsyncMock(return_value="access-1")),
        timeout_seconds=1.0,
        client=client,
    )


def _mock_play(*, cancel_status=200):
    """Play's answers for the yearly: its order, a renewing plan, the cancel."""
    respx.get(f"{API_ROOT}/{PACKAGE}/orders/{YEARLY_ORDER}").respond(
        200, json={"orderId": YEARLY_ORDER, "purchaseToken": PURCHASE_TOKEN}
    )
    respx.get(SUBSCRIPTION_URL).respond(
        200,
        json={
            "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
            "lineItems": [
                {
                    "productId": PLAY_YEARLY.split(":")[0],
                    "autoRenewingPlan": {"autoRenewEnabled": True},
                }
            ],
        },
    )
    return respx.post(f"{SUBSCRIPTION_URL}:cancel").respond(cancel_status)


@respx.mock
async def test_the_real_adapter_cancels_the_orders_token_in_the_package(
    uow, deliver, publisher, play
):
    cancel_route = _mock_play()
    await deliver(handle_purchase, play_yearly_purchase())

    async with httpx.AsyncClient() as client:
        play.getter.return_value = _real_adapter(client)
        with webhook_route(uow, publisher, []):
            assert await post_webhook(play_lifetime(UPGRADE)) == {"status": "success"}

    assert cancel_route.call_count == 1
    assert json.loads(cancel_route.calls.last.request.content) == CANCEL_BODY
    assert await entitled(uow)


@respx.mock
async def test_a_cancel_play_keeps_failing_is_retried_once_and_logged_without_token(
    uow, deliver, publisher, play, caplog
):
    cancel_route = _mock_play(cancel_status=503)
    await deliver(handle_purchase, play_yearly_purchase())

    async with httpx.AsyncClient() as client:
        play.getter.return_value = _real_adapter(client)
        with webhook_route(uow, publisher, []):
            assert await post_webhook(play_lifetime(UPGRADE)) == {"status": "success"}

    assert cancel_route.call_count == 2
    (error,) = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert YEARLY_ORDER in error and PLAY_YEARLY in error and USER.id in error
    assert PURCHASE_TOKEN not in caplog.text and "access-1" not in caplog.text
    assert await entitled(uow)


async def test_an_app_store_webhook_never_builds_the_adapter(uow, publisher, play):
    with webhook_route(uow, publisher, []):
        assert await post_webhook(lifetime()) == {"status": "success"}

    play.getter.assert_not_called()

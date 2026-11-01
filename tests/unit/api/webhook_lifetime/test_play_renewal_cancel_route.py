"""Through the RevenueCat route: the grant commits first, then Play is asked."""

import json
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_UPGRADE_PRODUCT_ID,
    USER,
    commit,
    entitled,
    lifetime,
    rows,
)
from tests.fixtures.fakes.play_renewal_fakes import (
    YEARLY_ORDER,
    play_lifetime,
    play_yearly_purchase,
)

from src.api.routes.v1.webhooks import handle_purchase, revenuecat_webhook
from src.domain.model.streak import StreakRestorePolicy
from src.domain.ports.play_subscription_port import PlayBillingConfigurationError
from src.infra.adapters.google_play_subscription_adapter import (
    API_ROOT,
    CANCEL_BODY,
    GooglePlaySubscriptionAdapter,
)

pytestmark = pytest.mark.asyncio

ROUTE = "src.api.routes.v1.webhooks"
PLAY_CANCEL = "src.api.routes.v1.webhook_play_renewal_cancel"
UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID


@contextmanager
def _route(uow, publisher, seen: list):
    """The route over the SQLite unit of work; each unit commits on a clean exit."""

    def unit_of_work():
        unit = MagicMock()
        unit.__aenter__ = AsyncMock(return_value=uow)

        async def exit_(exc_type, exc, tb):
            if exc_type is None:
                commit(uow)
                seen.append("commit")
            return False

        unit.__aexit__ = AsyncMock(side_effect=exit_)
        return unit

    with ExitStack() as stack:

        def mock(name, **kwargs):
            return stack.enter_context(patch(f"{ROUTE}.{name}", **kwargs))

        mock("os.getenv", return_value="test_secret")
        mock("AsyncUnitOfWork", side_effect=unit_of_work)
        mock("record_web_funnel_redemption", new_callable=AsyncMock, return_value=False)
        mock(
            "find_user_for_revenuecat_event", new_callable=AsyncMock, return_value=USER
        )
        mock("_get_affiliate_handler", return_value=None)
        mock("_get_event_publisher", return_value=publisher)
        mock("get_streak_restore_policy", return_value=StreakRestorePolicy())
        mock("get_cache_service", return_value=None)
        mock("purge_streak_cache", new_callable=AsyncMock)
        yield


async def _post(payload):
    request = MagicMock(json=AsyncMock(return_value={"event": payload}))
    return await revenuecat_webhook(request, authorization="test_secret")


async def test_the_grant_commits_before_play_is_asked(uow, deliver, publisher, play):
    await deliver(handle_purchase, play_yearly_purchase())
    seen: list = []

    async def cancel(order_id):
        # Nothing of the purchase is still uncommitted when Play is asked.
        seen.append(("cancel", order_id, uow.session.sync.in_transaction()))

    play.cancel_renewal.side_effect = cancel
    with _route(uow, publisher, seen):
        assert await _post(play_lifetime(UPGRADE)) == {"status": "success"}

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

    with _route(uow, publisher, []):
        assert await _post(play_lifetime(UPGRADE)) == {"status": "success"}

    (row,) = await rows(uow)
    assert (row.product_id, row.status, row.expires_at) == (UPGRADE, "active", None)
    assert await entitled(uow)


async def test_a_duplicate_webhook_cancels_once(uow, deliver, publisher, play):
    await deliver(handle_purchase, play_yearly_purchase())

    with _route(uow, publisher, []):
        for _ in range(2):
            assert await _post(play_lifetime(UPGRADE)) == {"status": "success"}

    play.cancel_renewal.assert_awaited_once_with(YEARLY_ORDER)
    assert len(await rows(uow)) == 1


@respx.mock
async def test_the_real_adapter_cancels_the_orders_token_in_the_package(
    uow, deliver, publisher, play
):
    package = "com.nutreeai.mobile"
    token = "purchase-token-of-the-yearly"
    order_route = respx.get(f"{API_ROOT}/{package}/orders/{YEARLY_ORDER}").respond(
        200, json={"orderId": YEARLY_ORDER, "purchaseToken": token}
    )
    cancel_route = respx.post(
        f"{API_ROOT}/{package}/purchases/subscriptionsv2/tokens/{token}:cancel"
    ).respond(200)
    await deliver(handle_purchase, play_yearly_purchase())

    async with httpx.AsyncClient() as client:
        adapter = GooglePlaySubscriptionAdapter(
            package_name=package,
            access_token=MagicMock(get=AsyncMock(return_value="access-1")),
            timeout_seconds=1.0,
            client=client,
        )
        play.getter.return_value = adapter
        with _route(uow, publisher, []):
            assert await _post(play_lifetime(UPGRADE)) == {"status": "success"}

    assert order_route.call_count == 1 and cancel_route.call_count == 1
    assert json.loads(cancel_route.calls.last.request.content) == CANCEL_BODY
    assert await entitled(uow)


async def test_an_app_store_webhook_never_builds_the_adapter(uow, publisher, play):
    with _route(uow, publisher, []):
        assert await _post(lifetime()) == {"status": "success"}

    play.getter.assert_not_called()

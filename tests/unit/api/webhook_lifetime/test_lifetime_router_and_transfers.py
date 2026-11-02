"""The router's lifetime branches, and transfers of a lifetime purchase."""

import uuid
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
    MONTHLY,
    SUBSCRIBER,
    USER,
    YEARLY,
    commit,
    entitled,
    event,
    lifecycle_types,
    lifetime,
    ms,
    rows,
)

from src.api.exceptions import ExternalServiceException
from src.api.routes.v1.webhook_lifetime_purchase import (
    RevenueCatPlan,
    handle_lifetime_purchase,
)
from src.api.routes.v1.webhook_referral_funnel import (
    handle_transfer,
    sync_redeemed_target_subscription,
)
from src.api.routes.v1.webhooks import handle_purchase, revenuecat_webhook
from src.domain.ports.subscription_service_port import EntitlementCheck
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

pytestmark = pytest.mark.asyncio

ROUTE = "src.api.routes.v1.webhooks"
OLD_UID = "firebase-uid-old"
OLD_OWNER = SimpleNamespace(id="user-old", firebase_uid=OLD_UID)


async def _post(payload):
    request = MagicMock(json=AsyncMock(return_value={"event": payload}))
    return await revenuecat_webhook(request, authorization="test_secret")


def _route_uow(uow=None):
    """The route's unit of work; it commits the SQLite session on a clean exit."""
    route_uow = MagicMock()
    route_uow.__aenter__ = AsyncMock(return_value=uow or route_uow)

    async def exit_(exc_type, exc, tb):
        if uow is not None and exc_type is None:
            commit(uow)
        return False

    route_uow.__aexit__ = AsyncMock(side_effect=exit_)
    return route_uow


@contextmanager
def _route(user, *, uow=None, publisher=None, spy=True):
    """The route with its collaborators replaced; handlers are spies when asked."""
    with ExitStack() as stack:

        def mock(name, **kwargs):
            return stack.enter_context(patch(f"{ROUTE}.{name}", **kwargs))

        mock("os.getenv", return_value="test_secret")
        units = mock("AsyncUnitOfWork", side_effect=[_route_uow(), _route_uow(uow)])
        mock("record_web_funnel_redemption", new_callable=AsyncMock, return_value=False)
        mock(
            "find_user_for_revenuecat_event", new_callable=AsyncMock, return_value=user
        )
        mock("_get_affiliate_handler", return_value=None)
        mock(
            "_get_event_publisher",
            return_value=publisher or MagicMock(publish=AsyncMock()),
        )
        spies = SimpleNamespace(units_of_work=units)
        if spy:
            for name in (
                "handle_lifetime_purchase",
                "handle_lifetime_refund",
                "handle_purchase",
                "handle_cancellation",
                "handle_refund",
            ):
                setattr(spies, name, mock(name, new_callable=AsyncMock))
            spies.handle_lifetime_purchase.return_value = []  # no Play plans
        yield spies


# --- Routing ---


@pytest.mark.parametrize(
    "product",
    [LIFETIME_PRODUCT_ID, LIFETIME_UPGRADE_PRODUCT_ID, f"{LIFETIME_PRODUCT_ID}:buy"],
)
async def test_a_lifetime_purchase_goes_to_its_handler(product):
    with _route(USER) as spies:
        assert await _post(lifetime(product)) == {"status": "success"}

    spies.handle_lifetime_purchase.assert_awaited_once()
    spies.handle_purchase.assert_not_awaited()


@pytest.mark.parametrize("event_type", ["CANCELLATION", "REFUND"])
async def test_a_lifetime_refund_goes_to_its_handler(event_type):
    with _route(USER) as spies:
        await _post(event(event_type, LIFETIME_UPGRADE_PRODUCT_ID))

    spies.handle_lifetime_refund.assert_awaited_once()
    spies.handle_cancellation.assert_not_awaited()
    spies.handle_refund.assert_not_awaited()


async def test_a_plan_cancellation_still_goes_to_the_subscription_handler():
    with _route(USER) as spies:
        await _post(event("CANCELLATION", YEARLY))

    spies.handle_cancellation.assert_awaited_once()
    spies.handle_lifetime_refund.assert_not_awaited()


async def test_another_one_time_product_writes_no_subscription_row():
    with _route(USER) as spies:
        result = await _post(event("NON_RENEWING_PURCHASE", "nutree_tip_jar"))

    assert result == {"status": "success"}
    spies.handle_lifetime_purchase.assert_not_awaited()
    spies.handle_purchase.assert_not_awaited()


async def test_a_lifetime_purchase_for_an_unknown_user_returns_404_for_a_retry():
    with _route(None) as spies, pytest.raises(HTTPException) as error:
        await _post(lifetime())

    assert error.value.status_code == 404
    spies.handle_lifetime_purchase.assert_not_awaited()


async def test_an_anonymous_lifetime_purchase_is_acknowledged():
    with _route(None) as spies:
        result = await _post(lifetime(app_user_id="$RCAnonymousID:abc"))

    assert result == {"status": "ignored", "reason": "user_not_found"}
    spies.handle_lifetime_purchase.assert_not_awaited()


async def test_a_lifetime_refund_through_the_real_router(uow, deliver, publisher):
    await deliver(handle_lifetime_purchase, lifetime())

    with _route(USER, uow=uow, publisher=publisher, spy=False):
        refund = event(
            "CANCELLATION", LIFETIME_PRODUCT_ID, cancel_reason="CUSTOMER_SUPPORT"
        )
        assert await _post(refund) == {"status": "success"}

    (row,) = await rows(uow)
    assert row.status == "refunded"
    assert not await entitled(uow)
    assert lifecycle_types(publisher)[-1] == "subscription_refund"


async def test_revenuecat_is_asked_before_the_unit_of_work_opens():
    """No pooled connection or subscriber lock waits on RevenueCat."""
    plan = RevenueCatPlan(None, EntitlementCheck.NOT_ENTITLED)
    units_opened_at_fetch: list[int] = []
    with _route(USER) as spies:

        async def fetch(payload):
            units_opened_at_fetch.append(spies.units_of_work.call_count)
            return plan

        with patch(f"{ROUTE}.fetch_revenuecat_plan", side_effect=fetch):
            await _post(event("CANCELLATION", LIFETIME_UPGRADE_PRODUCT_ID))

    assert units_opened_at_fetch == [1]  # only the web-funnel lookup's had opened
    assert spies.handle_lifetime_refund.await_args.args[5] is plan


async def test_an_upgrade_refund_revenuecat_cannot_answer_is_a_503_with_no_writes(
    uow, deliver, publisher, revenuecat
):
    await deliver(
        handle_purchase, event("INITIAL_PURCHASE", YEARLY, expiration_at_ms=ms(300))
    )
    await deliver(handle_lifetime_purchase, lifetime(LIFETIME_UPGRADE_PRODUCT_ID))
    published = publisher.publish.await_count
    revenuecat.check_entitlement.return_value = EntitlementCheck.UNAVAILABLE

    with _route(USER, uow=uow, publisher=publisher, spy=False):
        refund = event("CANCELLATION", LIFETIME_UPGRADE_PRODUCT_ID)
        with pytest.raises(ExternalServiceException):
            await _post(refund)
    uow.session.sync.rollback()

    (row,) = await rows(uow)
    assert (row.product_id, row.status) == (LIFETIME_UPGRADE_PRODUCT_ID, "active")
    assert publisher.publish.await_count == published


# --- Transfers ---


async def _transfer(uow, *, target=USER):
    payload = {
        "type": "TRANSFER",
        "transferred_from": [OLD_UID],
        "transferred_to": [target.firebase_uid],
    }
    # One unit of work, as in the router.
    await handle_transfer(uow, payload)
    await sync_redeemed_target_subscription(uow, target, payload)
    commit(uow)


async def test_a_lifetime_only_transfer_moves_premium_to_the_new_account(
    uow, publisher
):
    await handle_lifetime_purchase(
        uow, OLD_OWNER, lifetime(app_user_id=OLD_UID), event_publisher=publisher
    )
    commit(uow)

    await _transfer(uow)

    (row,) = await rows(uow)
    assert (row.revenuecat_subscriber_id, row.user_id) == (SUBSCRIBER, USER.id)
    assert not await entitled(uow, OLD_OWNER.id)
    assert await entitled(uow)


async def test_a_transfer_moves_every_row_of_the_old_account(uow, publisher):
    plan = event(
        "INITIAL_PURCHASE", MONTHLY, app_user_id=OLD_UID, expiration_at_ms=ms(30)
    )
    await handle_purchase(uow, OLD_OWNER, plan, event_publisher=publisher)
    commit(uow)
    await handle_lifetime_purchase(
        uow, OLD_OWNER, lifetime(app_user_id=OLD_UID), event_publisher=publisher
    )
    commit(uow)

    await _transfer(uow)

    moved = await rows(uow)
    assert len(moved) == 2
    assert {(row.revenuecat_subscriber_id, row.user_id) for row in moved} == {
        (SUBSCRIBER, USER.id)
    }
    assert not await entitled(uow, OLD_OWNER.id)


async def test_a_transfer_keeps_lifetime_when_revenuecat_lists_a_yearly_plan(
    uow, publisher, revenuecat
):
    await handle_lifetime_purchase(
        uow, OLD_OWNER, lifetime(app_user_id=OLD_UID), event_publisher=publisher
    )
    commit(uow)
    revenuecat.get_subscription_info.return_value = {
        "product_id": YEARLY,
        "expires_date": utc_now() + timedelta(days=300),
        "store": "app_store",
    }

    await _transfer(uow)

    (row,) = await rows(uow)
    assert (row.product_id, row.status, row.expires_at) == (
        LIFETIME_PRODUCT_ID,
        "active",
        None,
    )
    assert row.user_id == USER.id


def _old_account_row(product_id, platform, transaction_id=None):
    return Subscription(
        id=str(uuid.uuid4()),
        user_id=OLD_OWNER.id,
        revenuecat_subscriber_id=OLD_UID,
        product_id=product_id,
        platform=platform,
        status="active",
        purchased_at=utc_now() - timedelta(days=10),
        expires_at=utc_now() + timedelta(days=300),
        store_transaction_id=transaction_id,
    )


async def test_an_app_store_transfer_leaves_the_old_accounts_web_plan(uow):
    uow.session.add(_old_account_row("web_yearly", "web"))
    uow.session.add(_old_account_row(MONTHLY, "ios", "tx-m"))
    commit(uow)
    payload = {
        "type": "TRANSFER",
        "store": "APP_STORE",
        "transferred_from": [OLD_UID],
        "transferred_to": [SUBSCRIBER],
    }

    await handle_transfer(uow, payload)
    await sync_redeemed_target_subscription(uow, USER, payload)
    commit(uow)

    owners = {row.product_id: (row.user_id, row.platform) for row in await rows(uow)}
    assert owners == {
        "web_yearly": (OLD_OWNER.id, "web"),
        MONTHLY: (USER.id, "ios"),
    }
    assert await entitled(uow, OLD_OWNER.id)  # the web plan is still theirs
    assert await entitled(uow)


async def test_a_paddle_redemption_never_overwrites_an_app_store_plan(
    uow, deliver, revenuecat
):
    await deliver(
        handle_purchase, event("INITIAL_PURCHASE", MONTHLY, expiration_at_ms=ms(30))
    )
    revenuecat.get_subscription_info.return_value = {
        "product_id": "web_yearly",
        "expires_date": utc_now() + timedelta(days=365),
        "store": "paddle",
    }
    redeemed = {"type": "PURCHASE_REDEEMED", "store": "PADDLE"}

    await sync_redeemed_target_subscription(uow, USER, redeemed)
    commit(uow)

    platforms = {row.product_id: row.platform for row in await rows(uow)}
    assert platforms == {MONTHLY: "ios", "web_yearly": "web"}

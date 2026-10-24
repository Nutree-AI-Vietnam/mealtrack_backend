"""RevenueCat promotional grants never reach the store-subscription handlers."""

import logging
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.v1.webhook_lookup_parsing import is_promotional_event
from src.api.routes.v1.webhooks import revenuecat_webhook

ROUTE = "src.api.routes.v1.webhooks"
LIFECYCLE_HANDLERS = (
    "handle_purchase",
    "handle_renewal",
    "handle_cancellation",
    "handle_expiration",
    "handle_billing_issue",
    "handle_product_change",
    "handle_refund",
)
OTHER_HANDLERS = (
    "handle_transfer",
    "sync_redeemed_target_subscription",
    "reconcile_revenuecat_event",
)


def _event(event_type: str, store: str, app_user_id: str = "firebase-uid-1") -> dict:
    return {
        "id": "evt-1",
        "type": event_type,
        "app_user_id": app_user_id,
        "store": store,
        "product_id": "rc_promo_standard_custom",
        "entitlement_ids": ["standard"],
        "environment": "PRODUCTION",
    }


async def _deliver(event: dict, reconciled: bool = False):
    request = MagicMock()
    request.json = AsyncMock(return_value={"event": event})
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    mocks = {name: AsyncMock() for name in (*LIFECYCLE_HANDLERS, *OTHER_HANDLERS)}
    mocks["reconcile_revenuecat_event"].return_value = reconciled
    mocks["find_user_for_revenuecat_event"] = AsyncMock(
        return_value=MagicMock(id="user-1")
    )
    with ExitStack() as stack:
        stack.enter_context(patch(f"{ROUTE}.os.getenv", return_value="secret"))
        stack.enter_context(patch(f"{ROUTE}.AsyncUnitOfWork", return_value=uow))
        stack.enter_context(
            patch(
                f"{ROUTE}.record_web_funnel_redemption",
                AsyncMock(return_value=False),
            )
        )
        stack.enter_context(patch(f"{ROUTE}._get_event_publisher"))
        subscriptions = MagicMock(get_subscriber_info=AsyncMock(return_value={}))
        stack.enter_context(
            patch(f"{ROUTE}._get_subscription_service", return_value=subscriptions)
        )
        stack.enter_context(
            patch(
                f"{ROUTE}.get_web_funnel_outbox_dispatcher",
                return_value=AsyncMock(),
            )
        )
        for name, mock in mocks.items():
            stack.enter_context(patch(f"{ROUTE}.{name}", mock))
        result = await revenuecat_webhook(request, authorization="secret")
    return result, mocks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        "INITIAL_PURCHASE",
        "NON_RENEWING_PURCHASE",
        "RENEWAL",
        "CANCELLATION",
        "EXPIRATION",
        "BILLING_ISSUE",
        "PRODUCT_CHANGE",
        "REFUND",
    ],
)
async def test_promotional_lifecycle_event_leaves_the_subscription_row_alone(
    caplog, event_type
):
    with caplog.at_level(logging.INFO, logger=ROUTE):
        result, mocks = await _deliver(_event(event_type, "PROMOTIONAL"))

    assert result == {"status": "ignored", "reason": "promotional_entitlement"}
    for name in LIFECYCLE_HANDLERS:
        mocks[name].assert_not_awaited()
    ignored = [r for r in caplog.records if "promotional" in r.getMessage()]
    assert [record.levelno for record in ignored] == [logging.INFO]


@pytest.mark.asyncio
async def test_store_expiration_still_expires_the_subscription():
    result, mocks = await _deliver(_event("EXPIRATION", "APP_STORE"))

    assert result == {"status": "success"}
    mocks["handle_expiration"].assert_awaited_once()


@pytest.mark.asyncio
async def test_promotional_transfer_is_still_handled():
    result, mocks = await _deliver(_event("TRANSFER", "PROMOTIONAL"))

    assert result == {"status": "success"}
    mocks["handle_transfer"].assert_awaited_once()
    mocks["sync_redeemed_target_subscription"].assert_awaited_once()


@pytest.mark.asyncio
async def test_promotional_event_for_a_web_lead_is_still_reconciled():
    lead_event = _event(
        "EXPIRATION", "PROMOTIONAL", app_user_id="0b6f7a52-8d1c-4c3e-9a51-6a3f2b1c0d9e"
    )

    result, mocks = await _deliver(lead_event, reconciled=True)

    assert result == {"status": "success"}
    mocks["reconcile_revenuecat_event"].assert_awaited_once()
    mocks["handle_expiration"].assert_not_awaited()


@pytest.mark.parametrize(
    ("store", "promotional"),
    [("PROMOTIONAL", True), ("promotional", True), ("APP_STORE", False), (None, False)],
)
def test_promotional_events_are_recognised_by_store(store, promotional):
    assert is_promotional_event({"store": store}) is promotional

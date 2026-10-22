"""RevenueCat promotional grants never reach the store-subscription handlers."""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.v1.webhook_lookup_parsing import is_promotional_event
from src.api.routes.v1.webhooks import revenuecat_webhook

ROUTE = "src.api.routes.v1.webhooks"


def _event(event_type: str, store: str) -> dict:
    return {
        "id": "evt-1",
        "type": event_type,
        "app_user_id": "firebase-uid-1",
        "store": store,
        "product_id": "rc_promo_standard_custom",
        "entitlement_ids": ["standard"],
        "environment": "PRODUCTION",
    }


async def _deliver(event: dict) -> tuple[dict, dict[str, AsyncMock]]:
    request = MagicMock()
    request.json = AsyncMock(return_value={"event": event})
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    mocks = {
        name: AsyncMock()
        for name in ("handle_expiration", "handle_cancellation", "handle_purchase")
    }
    mocks["find_user"] = AsyncMock(return_value=MagicMock(id="user-1"))
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
        stack.enter_context(
            patch(f"{ROUTE}.find_user_for_revenuecat_event", mocks["find_user"])
        )
        for name in ("handle_expiration", "handle_cancellation", "handle_purchase"):
            stack.enter_context(patch(f"{ROUTE}.{name}", mocks[name]))
        result = await revenuecat_webhook(request, authorization="secret")
    return result, mocks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type", ["EXPIRATION", "CANCELLATION", "NON_RENEWING_PURCHASE"]
)
async def test_promotional_event_leaves_the_subscription_row_alone(event_type):
    result, mocks = await _deliver(_event(event_type, "PROMOTIONAL"))

    assert result == {"status": "ignored", "reason": "promotional_entitlement"}
    mocks["find_user"].assert_not_awaited()
    mocks["handle_expiration"].assert_not_awaited()
    mocks["handle_cancellation"].assert_not_awaited()


@pytest.mark.asyncio
async def test_store_expiration_still_expires_the_subscription():
    result, mocks = await _deliver(_event("EXPIRATION", "APP_STORE"))

    assert result == {"status": "success"}
    mocks["handle_expiration"].assert_awaited_once()


@pytest.mark.parametrize(
    ("store", "promotional"),
    [("PROMOTIONAL", True), ("promotional", True), ("APP_STORE", False), (None, False)],
)
def test_promotional_events_are_recognised_by_store(store, promotional):
    assert is_promotional_event({"store": store}) is promotional

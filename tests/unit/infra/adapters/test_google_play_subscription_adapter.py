"""The Google Play adapter: order to token, Play's own state, then the cancel."""

import json
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
import respx

from src.domain.ports.play_subscription_port import (
    PlayBillingConfigurationError,
    PlayBillingNotFoundError,
    PlayBillingRejectedError,
    PlayBillingUnavailableError,
    PlayCancelOutcome,
)
from src.infra.adapters.google_play_subscription_adapter import (
    API_ROOT,
    CANCEL_BODY,
    GooglePlaySubscriptionAdapter,
)

pytestmark = pytest.mark.asyncio

PACKAGE = "com.nutreeai.mobile"
ORDER = "GPA.3372-1234-5678-12345..0"
PRODUCT = "nutree_annual_599k:yearly"  # RevenueCat's id; Play's is before ':'
PURCHASE_TOKEN = "kpbnlmhlplnjgbnkkfhgijcm.AO-J1OzQ_example-token"
ORDER_URL = f"{API_ROOT}/{PACKAGE}/orders/{ORDER}"
SUBSCRIPTION_URL = (
    f"{API_ROOT}/{PACKAGE}/purchases/subscriptionsv2/tokens/{PURCHASE_TOKEN}"
)
CANCEL_URL = f"{SUBSCRIPTION_URL}:cancel"


class FakeAccessToken:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error

    async def get(self) -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return "access-1"


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient() as http_client:
        yield http_client


@pytest.fixture(autouse=True)
def no_retry_wait():
    with patch(
        "src.infra.adapters.google_play_subscription_adapter.RETRY_DELAY_SECONDS", 0
    ):
        yield


def _adapter(client, access_token=None, package_name=PACKAGE):
    return GooglePlaySubscriptionAdapter(
        package_name=package_name,
        access_token=access_token or FakeAccessToken(),
        timeout_seconds=1.0,
        client=client,
    )


def _order(**fields):
    return {"orderId": ORDER, "purchaseToken": PURCHASE_TOKEN, **fields}


def _subscription(state="SUBSCRIPTION_STATE_ACTIVE", *, auto_renew=True, product=None):
    plan = {"autoRenewEnabled": True} if auto_renew else {}  # proto3 drops false
    return {
        "subscriptionState": state,
        "lineItems": [
            {
                "productId": "other_addon",
                "autoRenewingPlan": {"autoRenewEnabled": True},
            },
            {"productId": product or PRODUCT.split(":")[0], "autoRenewingPlan": plan},
        ],
    }


def _mock_play(subscription=None):
    respx.get(ORDER_URL).respond(200, json=_order(state="PROCESSED"))
    respx.get(SUBSCRIPTION_URL).respond(200, json=subscription or _subscription())
    return respx.post(CANCEL_URL).respond(200)


# --- The cancel ---


@respx.mock
async def test_the_order_becomes_its_token_and_that_renewal_is_cancelled(client):
    cancel_route = _mock_play()
    access_token = FakeAccessToken()

    outcome = await _adapter(client, access_token).cancel_renewal(ORDER, PRODUCT)

    assert outcome is PlayCancelOutcome.CANCELLED
    assert cancel_route.call_count == 1
    for call in respx.calls:
        assert call.request.headers["Authorization"] == "Bearer access-1"
    assert json.loads(cancel_route.calls.last.request.content) == CANCEL_BODY
    assert CANCEL_BODY["cancellationContext"]["cancellationType"] == (
        "DEVELOPER_REQUESTED_STOP_PAYMENTS"
    )
    assert access_token.calls == 1  # one token for every call


@pytest.mark.parametrize(
    "state",
    [
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        "SUBSCRIPTION_STATE_ON_HOLD",
        "SUBSCRIPTION_STATE_PAUSED",
    ],
)
@respx.mock
async def test_a_plan_that_will_charge_again_is_cancelled(client, state):
    cancel_route = _mock_play(_subscription(state))

    assert await _adapter(client).cancel_renewal(ORDER, PRODUCT) is (
        PlayCancelOutcome.CANCELLED
    )
    assert cancel_route.called


@pytest.mark.parametrize(
    "subscription",
    [
        _subscription("SUBSCRIPTION_STATE_CANCELED", auto_renew=False),
        _subscription("SUBSCRIPTION_STATE_EXPIRED", auto_renew=False),
        _subscription("SUBSCRIPTION_STATE_PENDING"),
        _subscription("SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED"),
        _subscription("SUBSCRIPTION_STATE_ACTIVE", auto_renew=False),
        {"lineItems": _subscription()["lineItems"]},  # no state at all
    ],
    ids=[
        "canceled",
        "expired",
        "pending",
        "pending-canceled",
        "auto-renew-off",
        "no-state",
    ],
)
@respx.mock
async def test_a_plan_that_will_not_charge_again_is_left_alone(client, subscription):
    cancel_route = _mock_play(subscription)

    assert await _adapter(client).cancel_renewal(ORDER, PRODUCT) is (
        PlayCancelOutcome.NOT_RENEWING
    )
    assert not cancel_route.called


@respx.mock
async def test_a_subscription_without_the_product_is_a_mix_up(client):
    cancel_route = _mock_play(_subscription(product="nutree_weekly"))

    with pytest.raises(PlayBillingRejectedError):
        await _adapter(client).cancel_renewal(ORDER, PRODUCT)

    assert not cancel_route.called


# --- Failures and the one retry ---


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, PlayBillingConfigurationError),
        (403, PlayBillingConfigurationError),
        (404, PlayBillingNotFoundError),
        (410, PlayBillingNotFoundError),
        (408, PlayBillingUnavailableError),
        (429, PlayBillingUnavailableError),
        (400, PlayBillingRejectedError),
    ],
)
@respx.mock
async def test_a_4xx_is_typed_and_not_retried(client, status, error):
    order_route = respx.get(ORDER_URL).respond(status)

    with pytest.raises(error) as raised:
        await _adapter(client).cancel_renewal(ORDER, PRODUCT)

    assert raised.value.status_code == status
    assert order_route.call_count == 1


@pytest.mark.parametrize("step", ["order", "subscription", "cancel"])
@respx.mock
async def test_a_5xx_is_retried_once_and_then_succeeds(client, step):
    steps = {
        "order": (respx.get(ORDER_URL), httpx.Response(200, json=_order())),
        "subscription": (
            respx.get(SUBSCRIPTION_URL),
            httpx.Response(200, json=_subscription()),
        ),
        "cancel": (respx.post(CANCEL_URL), httpx.Response(200)),
    }
    for name, (route, answer) in steps.items():
        route.side_effect = [httpx.Response(503), answer] if name == step else [answer]

    outcome = await _adapter(client).cancel_renewal(ORDER, PRODUCT)

    assert outcome is PlayCancelOutcome.CANCELLED
    assert steps[step][0].call_count == 2


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(500), httpx.ConnectTimeout("slow"), httpx.ConnectError("down")],
    ids=["5xx", "timeout", "network"],
)
@respx.mock
async def test_a_second_transient_failure_gives_up_as_unavailable(client, failure):
    _mock_play()
    cancel_route = respx.post(CANCEL_URL)
    cancel_route.side_effect = [failure, failure]

    with pytest.raises(PlayBillingUnavailableError):
        await _adapter(client).cancel_renewal(ORDER, PRODUCT)

    assert cancel_route.call_count == 2


@respx.mock
async def test_google_error_words_are_kept_but_not_its_message(client):
    respx.get(ORDER_URL).respond(
        403,
        json={
            "error": {
                "code": 403,
                "status": "PERMISSION_DENIED",
                "message": f"No access to order {ORDER}",
                "errors": [{"reason": "permissionDenied", "message": "x"}],
            }
        },
    )

    with pytest.raises(PlayBillingConfigurationError) as raised:
        await _adapter(client).cancel_renewal(ORDER, PRODUCT)

    text = str(raised.value)
    assert "orders.get returned 403 (PERMISSION_DENIED, permissionDenied)" in text
    assert ORDER not in text


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"orderId": ORDER}),
        httpx.Response(200, json=_order(purchaseToken="")),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, text="<html>"),
        httpx.Response(200),
    ],
)
@respx.mock
async def test_an_order_without_a_usable_token_is_rejected(client, response):
    respx.get(ORDER_URL).mock(return_value=response)
    subscription_route = respx.get(SUBSCRIPTION_URL).respond(200, json=_subscription())

    with pytest.raises(PlayBillingRejectedError):
        await _adapter(client).cancel_renewal(ORDER, PRODUCT)

    assert not subscription_route.called


@pytest.mark.parametrize(
    ("order_id", "product_id"),
    [
        ("", PRODUCT),
        (" ", PRODUCT),
        ("GPA.1 2", PRODUCT),
        ("x" * 5000, PRODUCT),
        (ORDER, ""),
        (ORDER, ":yearly"),
    ],
    ids=["empty", "blank", "inner-space", "too-long", "no-product", "bare-plan"],
)
@respx.mock(assert_all_mocked=True)
async def test_malformed_ids_are_rejected_before_any_call(client, order_id, product_id):
    access_token = FakeAccessToken()

    with pytest.raises(PlayBillingRejectedError):
        await _adapter(client, access_token).cancel_renewal(order_id, product_id)

    assert access_token.calls == 0


@respx.mock
async def test_an_order_id_with_a_slash_stays_one_path_segment(client):
    route = respx.get(f"{API_ROOT}/{PACKAGE}/orders/a%2Fb").respond(404)

    with pytest.raises(PlayBillingNotFoundError):
        await _adapter(client).cancel_renewal("a/b", PRODUCT)

    assert route.called


@respx.mock
async def test_a_token_failure_stops_before_any_play_call(client):
    order_route = respx.get(ORDER_URL).respond(200, json=_order())
    access_token = FakeAccessToken(PlayBillingUnavailableError("token down"))

    with pytest.raises(PlayBillingUnavailableError):
        await _adapter(client, access_token).cancel_renewal(ORDER, PRODUCT)

    assert not order_route.called


@pytest.mark.parametrize(
    "fields",
    [
        {"access_token": None},
        {"package_name": ""},
        {"package_name": "nutree"},
        {"switched_on": False},
    ],
    ids=["no-key", "no-package", "bad-package", "switched-off"],
)
@respx.mock(assert_all_mocked=True)
async def test_a_disabled_adapter_calls_nothing(fields):
    values = {
        "package_name": PACKAGE,
        "access_token": FakeAccessToken(),
        "timeout_seconds": 1.0,
        **fields,
    }
    adapter = GooglePlaySubscriptionAdapter(**values)
    assert adapter.enabled is False
    assert adapter.switched_on is fields.get("switched_on", True)

    with pytest.raises(PlayBillingConfigurationError):
        await adapter.cancel_renewal(ORDER, PRODUCT)

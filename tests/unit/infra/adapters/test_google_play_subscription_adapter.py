"""The Google Play adapter: an order id becomes its purchase token, then the renewal stops."""

import json

import httpx
import pytest
import pytest_asyncio
import respx

from src.domain.ports.play_subscription_port import (
    PlayBillingConfigurationError,
    PlayBillingNotFoundError,
    PlayBillingRejectedError,
    PlayBillingUnavailableError,
)
from src.infra.adapters.google_play_subscription_adapter import (
    API_ROOT,
    CANCEL_BODY,
    GooglePlaySubscriptionAdapter,
)

pytestmark = pytest.mark.asyncio

PACKAGE = "com.nutreeai.mobile"
ORDER = "GPA.3372-1234-5678-12345..0"
PURCHASE_TOKEN = "kpbnlmhlplnjgbnkkfhgijcm.AO-J1OzQ_example-token"
ORDER_URL = f"{API_ROOT}/{PACKAGE}/orders/{ORDER}"
CANCEL_URL = (
    f"{API_ROOT}/{PACKAGE}/purchases/subscriptionsv2/tokens/{PURCHASE_TOKEN}:cancel"
)


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


def _adapter(client, access_token=None, package_name=PACKAGE):
    return GooglePlaySubscriptionAdapter(
        package_name=package_name,
        access_token=access_token or FakeAccessToken(),
        timeout_seconds=1.0,
        client=client,
    )


def _order(**fields):
    return {"orderId": ORDER, "purchaseToken": PURCHASE_TOKEN, **fields}


@respx.mock
async def test_the_order_becomes_its_token_and_that_renewal_is_cancelled(client):
    order_route = respx.get(ORDER_URL).respond(200, json=_order(state="PROCESSED"))
    cancel_route = respx.post(CANCEL_URL).respond(200)
    access_token = FakeAccessToken()

    await _adapter(client, access_token).cancel_renewal(ORDER)

    assert order_route.call_count == 1 and cancel_route.call_count == 1
    for route in (order_route, cancel_route):
        assert route.calls.last.request.headers["Authorization"] == "Bearer access-1"
    assert json.loads(cancel_route.calls.last.request.content) == CANCEL_BODY
    assert CANCEL_BODY["cancellationContext"]["cancellationType"] == (
        "DEVELOPER_REQUESTED_STOP_PAYMENTS"
    )
    assert access_token.calls == 1  # one token for both calls


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, PlayBillingConfigurationError),
        (403, PlayBillingConfigurationError),
        (404, PlayBillingNotFoundError),
        (410, PlayBillingNotFoundError),
        (408, PlayBillingUnavailableError),
        (429, PlayBillingUnavailableError),
        (500, PlayBillingUnavailableError),
        (503, PlayBillingUnavailableError),
        (400, PlayBillingRejectedError),
    ],
)
@respx.mock
async def test_an_order_lookup_failure_is_typed_and_cancels_nothing(
    client, status, error
):
    respx.get(ORDER_URL).respond(status)
    cancel_route = respx.post(CANCEL_URL).respond(200)

    with pytest.raises(error) as raised:
        await _adapter(client).cancel_renewal(ORDER)

    assert raised.value.status_code == status
    assert not cancel_route.called


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, PlayBillingConfigurationError),
        (410, PlayBillingNotFoundError),
        (500, PlayBillingUnavailableError),
        (400, PlayBillingRejectedError),
    ],
)
@respx.mock
async def test_a_cancel_failure_is_typed(client, status, error):
    respx.get(ORDER_URL).respond(200, json=_order())
    respx.post(CANCEL_URL).respond(status)

    with pytest.raises(error):
        await _adapter(client).cancel_renewal(ORDER)


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
        await _adapter(client).cancel_renewal(ORDER)

    text = str(raised.value)
    assert "orders.get returned 403 (PERMISSION_DENIED, permissionDenied)" in text
    assert ORDER not in text


@pytest.mark.parametrize(
    "failure", [httpx.ConnectTimeout("slow"), httpx.ConnectError("down")]
)
@respx.mock
async def test_a_timeout_or_network_failure_is_unavailable(client, failure):
    respx.get(ORDER_URL).mock(side_effect=failure)

    with pytest.raises(PlayBillingUnavailableError):
        await _adapter(client).cancel_renewal(ORDER)


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
    cancel_route = respx.post(CANCEL_URL).respond(200)

    with pytest.raises(PlayBillingRejectedError):
        await _adapter(client).cancel_renewal(ORDER)

    assert not cancel_route.called


@pytest.mark.parametrize(
    "order_id",
    ["", " ", "GPA.1 2", "x" * 5000],
    ids=["empty", "blank", "inner-space", "too-long"],
)
@respx.mock(assert_all_mocked=True)
async def test_a_malformed_order_id_is_rejected_before_any_call(client, order_id):
    access_token = FakeAccessToken()

    with pytest.raises(PlayBillingRejectedError):
        await _adapter(client, access_token).cancel_renewal(order_id)

    assert access_token.calls == 0


@respx.mock
async def test_an_order_id_with_a_slash_stays_one_path_segment(client):
    route = respx.get(f"{API_ROOT}/{PACKAGE}/orders/a%2Fb").respond(404)

    with pytest.raises(PlayBillingNotFoundError):
        await _adapter(client).cancel_renewal("a/b")

    assert route.called


@respx.mock
async def test_a_token_failure_stops_before_any_play_call(client):
    order_route = respx.get(ORDER_URL).respond(200, json=_order())
    access_token = FakeAccessToken(PlayBillingUnavailableError("token down"))

    with pytest.raises(PlayBillingUnavailableError):
        await _adapter(client, access_token).cancel_renewal(ORDER)

    assert not order_route.called


@pytest.mark.parametrize(
    "adapter",
    [
        GooglePlaySubscriptionAdapter(
            package_name=PACKAGE, access_token=None, timeout_seconds=1.0
        ),
        GooglePlaySubscriptionAdapter(
            package_name="", access_token=FakeAccessToken(), timeout_seconds=1.0
        ),
        GooglePlaySubscriptionAdapter(
            package_name="nutree", access_token=FakeAccessToken(), timeout_seconds=1.0
        ),
    ],
)
@respx.mock(assert_all_mocked=True)
async def test_a_disabled_adapter_calls_nothing(adapter):
    assert adapter.enabled is False

    with pytest.raises(PlayBillingConfigurationError):
        await adapter.cancel_renewal(ORDER)

"""RevenueCatBillingProvider: v1 GET, typed errors, normalized snapshot."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.domain.exceptions.billing_exceptions import (
    InvalidProviderPayload,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUnavailable,
)
from src.infra.adapters.revenuecat_billing_provider import RevenueCatBillingProvider

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "revenuecat_v1"
FETCHED_AT = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
CANONICAL = "Xq7fK2mLp9RzT4vB8nWc3dHs6Ya1"


def _provider(handler, **kwargs) -> RevenueCatBillingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RevenueCatBillingProvider(
        api_key="sk_test", client=client, clock=lambda: FETCHED_AT, **kwargs
    )


def _body(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


async def test_fetch_customer_normalizes_v1_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_body("active_play"))

    snapshot = await _provider(handler).fetch_customer(CANONICAL)

    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == f"https://api.revenuecat.com/v1/subscribers/{CANONICAL}"
    assert request.headers["Authorization"] == "Bearer sk_test"
    assert snapshot.canonical_customer_id == CANONICAL
    assert snapshot.fetched_at == FETCHED_AT
    assert snapshot.requested_customer_id == CANONICAL
    (sub,) = snapshot.subscriptions
    assert sub.subscription_key == f"rc:{CANONICAL}:nutree_999_1m_0"
    assert sub.entitlement_key == "standard"
    assert snapshot.provider_reports_active(now=FETCHED_AT) is True


async def test_customer_id_is_url_encoded():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json=_body("never_subscribed"))

    await _provider(handler).fetch_customer("$RCAnonymousID:a/b")

    assert seen == ["/v1/subscribers/%24RCAnonymousID%3Aa%2Fb"]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(404, json={"code": 7259}), ProviderNotFound),
        (httpx.Response(429, json={"code": 7638}), ProviderRateLimited),
        (
            httpx.Response(200, json={"code": 7638, "message": "slow"}),
            ProviderRateLimited,
        ),
        (httpx.Response(500, text="boom"), ProviderUnavailable),
        (httpx.Response(503, json={}), ProviderUnavailable),
        (httpx.Response(401, json={"code": 7225}), ProviderUnavailable),
        (httpx.Response(200, text="not json"), InvalidProviderPayload),
        (httpx.Response(200, json=[1, 2]), InvalidProviderPayload),
        (httpx.Response(200, json={"subscriber": {}}), InvalidProviderPayload),
    ],
)
async def test_http_failures_map_to_typed_errors(response, error):
    with pytest.raises(error):
        await _provider(lambda _request: response).fetch_customer("uid")


async def test_retry_after_header_is_parsed():
    response = httpx.Response(429, headers={"Retry-After": "7"}, json={})

    with pytest.raises(ProviderRateLimited) as caught:
        await _provider(lambda _request: response).fetch_customer("uid")

    assert caught.value.retry_after_seconds == 7.0


async def test_backoff_ms_body_is_parsed():
    response = httpx.Response(429, json={"code": 7638, "backoff_ms": 2500})

    with pytest.raises(ProviderRateLimited) as caught:
        await _provider(lambda _request: response).fetch_customer("uid")

    assert caught.value.retry_after_seconds == 2.5


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")],
)
async def test_transport_errors_are_unavailable(exc):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(ProviderUnavailable):
        await _provider(handler).fetch_customer("uid")


async def test_missing_api_key_never_calls_revenuecat(monkeypatch):
    calls: list[httpx.Request] = []
    monkeypatch.setattr(
        "src.infra.adapters.revenuecat_billing_provider.settings.REVENUECAT_SECRET_API_KEY",
        None,
    )
    provider = RevenueCatBillingProvider(
        api_key=None,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: calls.append(r))
        ),
    )

    with pytest.raises(ProviderUnavailable):
        await provider.fetch_customer("uid")
    assert calls == []


async def test_reads_key_from_settings(monkeypatch):
    monkeypatch.setattr(
        "src.infra.adapters.revenuecat_billing_provider.settings.REVENUECAT_SECRET_API_KEY",
        "sk_from_settings",
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json=_body("never_subscribed"))

    provider = RevenueCatBillingProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    await provider.fetch_customer("uid")

    assert seen == ["Bearer sk_from_settings"]

"""Granting a RevenueCat promotional entitlement, against a mock transport."""

import json

import httpx
import pytest

from src.infra.adapters import revenuecat_adapter
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter

pytestmark = pytest.mark.asyncio


def _serve(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    client = httpx.AsyncClient

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        revenuecat_adapter.httpx,
        "AsyncClient",
        lambda **_kwargs: client(transport=httpx.MockTransport(record)),
    )
    return seen


async def test_grant_posts_the_end_time_for_the_escaped_subscriber(monkeypatch):
    seen = _serve(monkeypatch, lambda _request: httpx.Response(201, json={}))

    granted = await RevenueCatAdapter("rc-key").grant_promotional_entitlement(
        "uid/1", "standard", 1_800_000_000_000
    )

    assert granted
    assert seen[0].method == "POST"
    assert (
        seen[0].url.raw_path
        == b"/v1/subscribers/uid%2F1/entitlements/standard/promotional"
    )
    assert seen[0].headers["Authorization"] == "Bearer rc-key"
    assert json.loads(seen[0].content) == {"end_time_ms": 1_800_000_000_000}


@pytest.mark.parametrize("status", [400, 503])
async def test_refused_grant_is_reported_for_a_retry(monkeypatch, status):
    _serve(monkeypatch, lambda _request: httpx.Response(status, json={}))

    assert not await RevenueCatAdapter("rc-key").grant_promotional_entitlement(
        "uid-1", "standard", 1_800_000_000_000
    )


async def test_unreachable_revenuecat_is_reported_for_a_retry(monkeypatch):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    _serve(monkeypatch, unreachable)

    assert not await RevenueCatAdapter("rc-key").grant_promotional_entitlement(
        "uid-1", "standard", 1_800_000_000_000
    )

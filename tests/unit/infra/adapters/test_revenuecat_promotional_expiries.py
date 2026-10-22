"""Reading promotional grants back from RevenueCat, against a mock transport."""

from datetime import UTC, datetime

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


async def test_lists_only_dated_promotional_grants(monkeypatch):
    subscriptions = {
        "rc_promo_standard_custom": {
            "store": "promotional",
            "expires_date": "2026-11-20T10:00:00Z",
        },
        "rc_promo_standard_lifetime": {"store": "promotional", "expires_date": None},
        "rc_promo_standard_weekly": {
            "store": "PROMOTIONAL",
            "expires_date": "2026-11-01T08:30:00",
        },
        "nutree_premium_yearly": {
            "store": "app_store",
            "expires_date": "2027-01-01T00:00:00Z",
        },
    }
    seen = _serve(
        monkeypatch,
        lambda _request: httpx.Response(
            200, json={"subscriber": {"subscriptions": subscriptions}}
        ),
    )

    expiries = await RevenueCatAdapter("rc-key").promotional_expiries("uid/1")

    assert expiries == [
        datetime(2026, 11, 20, 10, tzinfo=UTC),
        datetime(2026, 11, 1, 8, 30, tzinfo=UTC),
    ]
    assert seen[0].url.raw_path == b"/v1/subscribers/uid%2F1"
    assert seen[0].headers["Authorization"] == "Bearer rc-key"


async def test_unknown_subscriber_has_no_promotional_grants(monkeypatch):
    _serve(monkeypatch, lambda _request: httpx.Response(404, json={}))

    assert await RevenueCatAdapter("rc-key").promotional_expiries("uid-1") == []


@pytest.mark.parametrize(
    "response",
    [httpx.Response(503, json={}), httpx.Response(200, json={"code": 7225})],
    ids=["outage", "unexpected-body"],
)
async def test_unreadable_subscriber_is_not_mistaken_for_no_grant(
    monkeypatch, response
):
    _serve(monkeypatch, lambda _request: response)

    assert await RevenueCatAdapter("rc-key").promotional_expiries("uid-1") is None

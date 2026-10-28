"""Contract tests for POST /v1/me/entitlements/refresh (shared with mobile)."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.api.routes.v1 import me as me_route
from src.app.commands.subscription_access import RefreshMyEntitlementsCommand
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.domain.model.subscription_access import SubscriptionAccessUnavailable
from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessRefresh,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable

GRANTED = {
    "has_access": True,
    "status": "active",
    "expires_at": None,
    "product_id": "standard_lifetime",
    "will_renew": False,
    "provider": "revenuecat",
}


class _EventBus:
    """Answers the refresh command and the entitlements query separately."""

    def __init__(self, *, refresh=None, read=None):
        self.refresh = refresh or SubscriptionAccessRefresh("applied")
        self.read = read or GRANTED
        self.sent: list = []

    async def send(self, message):
        self.sent.append(message)
        outcome = (
            self.refresh
            if isinstance(message, RefreshMyEntitlementsCommand)
            else self.read
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _token(uid: str) -> str:
    """An unsigned JWT: the limiter keys on its uid; auth is overridden."""

    def part(value: dict) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'sub': uid})}.signature"


def _client(event_bus) -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(me_route.router)
    app.dependency_overrides[get_current_user_id] = lambda: "db-user-id"
    app.dependency_overrides[get_configured_event_bus] = lambda: event_bus
    return TestClient(app)


def _refresh(client: TestClient, uid: str = "firebase-uid-1"):
    return client.post(
        "/v1/me/entitlements/refresh",
        headers={"Authorization": f"Bearer {_token(uid)}"},
    )


@pytest.fixture(autouse=True)
def _fresh_limiter():
    limiter.clear_memory()
    yield
    limiter.clear_memory()


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "false")


def test_refresh_syncs_then_answers_exactly_like_get(flag_on):
    bus = _EventBus()

    response = _refresh(_client(bus))

    assert response.status_code == 200
    assert response.json() == GRANTED
    assert response.headers["cache-control"] == "private, no-store"
    assert bus.sent == [
        RefreshMyEntitlementsCommand("db-user-id"),
        GetMyEntitlementsQuery("db-user-id"),
    ]


def test_refresh_not_applied_still_answers_from_the_database(flag_on):
    bus = _EventBus(
        refresh=SubscriptionAccessRefresh("not_applied", reason="conflicting_facts")
    )

    response = _refresh(_client(bus))

    assert response.status_code == 200
    assert response.json() == GRANTED


@pytest.mark.parametrize(
    "refresh",
    [
        SubscriptionAccessRefresh("applied"),
        SubscriptionAccessRefresh("not_applied", reason="conflicting_facts"),
    ],
)
def test_a_refresh_lets_a_refused_paid_route_ask_revenuecat_again(
    flag_on, monkeypatch, refresh
):
    """RevenueCat was just read: a paid route the database still refuses
    must not wait out the RevenueCat answer it got before the refresh."""
    forget = AsyncMock()
    monkeypatch.setattr(me_route, "forget_revenuecat_check", forget)

    assert _refresh(_client(_EventBus(refresh=refresh))).status_code == 200

    forget.assert_awaited_once_with("db-user-id")


def test_an_unreadable_refresh_keeps_the_last_revenuecat_answer(flag_on, monkeypatch):
    forget = AsyncMock()
    monkeypatch.setattr(me_route, "forget_revenuecat_check", forget)
    bus = _EventBus(refresh=RevenueCatSnapshotUnavailable("down", reason="http_5xx"))

    assert _refresh(_client(bus)).status_code == 503

    forget.assert_not_called()


def test_refresh_then_unresolved_rows_answer_unavailable(flag_on):
    bus = _EventBus(read=SubscriptionAccessUnavailable("still unverified"))

    response = _refresh(_client(bus))

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "error_code": "ENTITLEMENTS_UNAVAILABLE",
            "message": "Subscription access cannot be decided right now.",
        }
    }


def test_flag_off_still_syncs_but_answers_disabled(flag_off):
    bus = _EventBus()

    response = _refresh(_client(bus))

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "error_code": "ENTITLEMENTS_DISABLED",
            "message": "Database subscription access is turned off.",
        }
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert bus.sent == [RefreshMyEntitlementsCommand("db-user-id")]


def test_unreadable_revenuecat_answers_unavailable_with_retry_after(flag_on):
    bus = _EventBus(
        refresh=RevenueCatSnapshotUnavailable(
            "RevenueCat rate limited", retry_after_seconds=12.2, reason="http_429"
        )
    )

    response = _refresh(_client(bus))

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "ENTITLEMENTS_UNAVAILABLE"
    assert response.headers["retry-after"] == "13"
    assert response.headers["cache-control"] == "private, no-store"
    # The database is not read: nothing new was learned.
    assert bus.sent == [RefreshMyEntitlementsCommand("db-user-id")]


def test_unreadable_revenuecat_without_a_delay_has_no_retry_after(flag_on):
    bus = _EventBus(
        refresh=RevenueCatSnapshotUnavailable("timeout", reason="transport")
    )

    response = _refresh(_client(bus))

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "ENTITLEMENTS_UNAVAILABLE"
    assert "retry-after" not in response.headers


def test_unreadable_revenuecat_with_flag_off_answers_disabled(flag_off):
    bus = _EventBus(
        refresh=RevenueCatSnapshotUnavailable(
            "down", retry_after_seconds=5, reason="http_5xx"
        )
    )

    response = _refresh(_client(bus))

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "ENTITLEMENTS_DISABLED"


def test_unexpected_refresh_error_is_reported_then_unavailable(flag_on, monkeypatch):
    capture = MagicMock()
    monkeypatch.setattr(me_route, "capture_exception", capture)
    failure = RuntimeError("deadlock detected")
    bus = _EventBus(refresh=failure)

    response = _refresh(_client(bus))

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "ENTITLEMENTS_UNAVAILABLE"
    capture.assert_called_once()
    assert capture.call_args.args[0] is failure
    assert capture.call_args.kwargs["context"]["operation"] == "refresh"


def test_refresh_is_rate_limited_per_user(flag_on):
    bus = _EventBus()
    client = _client(bus)

    statuses = [_refresh(client).status_code for _ in range(6)]
    limited = _refresh(client)
    other_user = _refresh(client, uid="firebase-uid-2")

    assert statuses == [200] * 6
    assert limited.status_code == 429
    assert limited.json() == {
        "error": "rate_limit_exceeded",
        "code": "preview_rate_limited",
    }
    assert limited.headers["retry-after"] == "60"
    # The limited call never reached RevenueCat or the database.
    assert len(bus.sent) == 2 * 6 + 2
    assert other_user.status_code == 200

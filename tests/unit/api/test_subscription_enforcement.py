"""Server-side subscription enforcement on paid backend routes.

SUBSCRIPTION_ENFORCEMENT_ENABLED (effective only with SUBSCRIPTION_ACCESS_ENABLED)
turns on 402 SUBSCRIPTION_REQUIRED for the paid AI routes the mobile app
hard-gates with requireSubscriptionAccess. Off by default: deploying it
changes nothing.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.api.dependencies import subscription_access as enforcement
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.subscription_access import require_active_subscription
from src.app.handlers.query_handlers.check_subscription_access_query_handler import (
    CheckSubscriptionAccessQueryHandler,
)
from src.app.queries.subscription_access import CheckSubscriptionAccessQuery
from src.app.services.subscription_access_flags import (
    subscription_enforcement_enabled,
)

NOW = datetime.now(UTC)

# Keep in sync with "Paid routes enforced" in docs/api-endpoints.md.
GATED_ROUTES = {
    ("POST", "/v1/chat/messages"),
    ("POST", "/v1/chat/threads/{thread_id}/messages"),
    ("POST", "/v1/meals/image/analyze"),
    ("POST", "/v1/meals/scan-by-url"),
    ("POST", "/v1/meals/food-label/scan-by-url"),
    ("POST", "/v1/meals/parse-text"),
    ("POST", "/v1/meal-suggestions/discover"),
    ("POST", "/v1/meal-suggestions/recipes"),
    ("POST", "/v1/ingredients/recognize"),
    ("POST", "/v1/meal-plans/generate"),
    ("POST", "/v1/meal-plans/{plan_id}/ai-prompt"),
}


class _Bus:
    def __init__(self, has_access: bool):
        self.has_access = has_access
        self.sent: list = []

    async def send(self, query):
        self.sent.append(query)
        return self.has_access


def _client(monkeypatch, bus) -> TestClient:
    monkeypatch.setattr(enforcement, "_event_bus", lambda: bus)
    app = FastAPI()

    @app.post("/paid", dependencies=[Depends(require_active_subscription)])
    async def paid():
        return {"ok": True}

    app.dependency_overrides[get_current_user_id] = lambda: "db-user-id"
    return TestClient(app)


def _flags(monkeypatch, *, access: str, enforcement_flag: str | None) -> None:
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", access)
    if enforcement_flag is None:
        monkeypatch.delenv("SUBSCRIPTION_ENFORCEMENT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SUBSCRIPTION_ENFORCEMENT_ENABLED", enforcement_flag)


@pytest.mark.parametrize(
    ("access", "enforcement_flag", "expected"),
    [
        ("true", None, False),  # default off
        ("true", "false", False),
        ("false", "true", False),  # the kill switch also stops enforcement
        ("true", "true", True),
    ],
)
def test_enforcement_needs_both_flags(monkeypatch, access, enforcement_flag, expected):
    _flags(monkeypatch, access=access, enforcement_flag=enforcement_flag)
    assert subscription_enforcement_enabled() is expected


def test_flag_off_is_a_no_op_without_any_lookup(monkeypatch):
    _flags(monkeypatch, access="true", enforcement_flag=None)
    bus = _Bus(has_access=False)

    response = _client(monkeypatch, bus).post("/paid")

    assert response.status_code == 200
    assert bus.sent == []


def test_flag_on_allows_a_user_with_access(monkeypatch):
    _flags(monkeypatch, access="true", enforcement_flag="true")
    bus = _Bus(has_access=True)

    response = _client(monkeypatch, bus).post("/paid")

    assert response.status_code == 200
    assert bus.sent == [CheckSubscriptionAccessQuery("db-user-id")]


def test_flag_on_refuses_a_user_without_access_with_402(monkeypatch):
    _flags(monkeypatch, access="true", enforcement_flag="true")

    response = _client(monkeypatch, _Bus(has_access=False)).post("/paid")

    assert response.status_code == 402
    assert response.json() == {
        "detail": {
            "error_code": "SUBSCRIPTION_REQUIRED",
            "message": "Standard subscription required",
        }
    }


def test_gated_route_refuses_before_its_handler_runs(monkeypatch):
    """A real paid route: the AI handler never runs for a 402."""
    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.api.routes.v1.ingredients import router

    _flags(monkeypatch, access="true", enforcement_flag="true")
    monkeypatch.setattr(enforcement, "_event_bus", lambda: _Bus(has_access=False))
    route_bus = _Bus(has_access=True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "db-user-id"
    app.dependency_overrides[get_configured_event_bus] = lambda: route_bus

    response = TestClient(app).post(
        "/v1/ingredients/recognize", json={"image_data": "aGVsbG8="}
    )

    assert response.status_code == 402
    assert response.json()["detail"]["error_code"] == "SUBSCRIPTION_REQUIRED"
    assert route_bus.sent == []


def _dependency_calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


def test_exactly_the_documented_paid_routes_are_gated():
    from src.api.main import app

    gated = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        and require_active_subscription in set(_dependency_calls(route.dependant))
        for method in route.methods
    }

    assert gated == GATED_ROUTES


def test_lapsed_and_free_routes_are_never_gated():
    """Hydration, movement and weight stay open to lapsed users; reading or
    logging food, entitlements and sign-in are never gated here."""
    from src.api.main import app

    open_prefixes = (
        "/v1/hydration",
        "/v1/movement",
        "/v1/weight-entries",
        "/v1/users",
        "/v1/me/",
        "/v1/meals/parse-text/guest-trial",
        "/v1/webhooks",
    )
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(open_prefixes):
            assert require_active_subscription not in set(
                _dependency_calls(route.dependant)
            ), route.path


# --- The decision behind the dependency ------------------------------------


@dataclass
class _Row:
    status: str = "active"
    expires_at: datetime | None = None
    grace_period_expires_at: datetime | None = None
    period_type: str | None = "normal"
    provider_snapshot_at: datetime | None = NOW
    provider_sync_required_at: datetime | None = None
    provider: str = "revenuecat"
    product_id: str = "standard_monthly"

    def is_active(self) -> bool:
        """The legacy row check (ORM Subscription.is_active for unverified rows)."""
        return self.status == "active" and (
            self.expires_at is None or self.expires_at > datetime.now(UTC)
        )


class _Uow:
    def __init__(self, rows):
        self.subscriptions = self
        self.rows = rows

    async def find_by_user_id(self, user_id):
        assert user_id == "db-user-id"
        return self.rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


async def _decide(rows) -> bool:
    handler = CheckSubscriptionAccessQueryHandler(uow_factory=lambda: _Uow(rows))
    return await handler.handle(CheckSubscriptionAccessQuery("db-user-id"))


@pytest.mark.asyncio
async def test_policy_grant_allows():
    assert await _decide([_Row(expires_at=NOW + timedelta(days=3))]) is True


@pytest.mark.asyncio
async def test_policy_denial_refuses():
    assert await _decide([_Row(status="expired", expires_at=NOW)]) is False
    assert await _decide([]) is False


@pytest.mark.asyncio
async def test_unresolved_rows_fall_back_to_the_legacy_check(caplog):
    unverified_active = _Row(
        provider_snapshot_at=None, expires_at=NOW + timedelta(days=3)
    )
    unverified_lapsed = _Row(
        status="cancelled",
        provider_snapshot_at=None,
        expires_at=NOW - timedelta(days=300),
    )

    with caplog.at_level(logging.WARNING):
        assert await _decide([unverified_active]) is True
        assert await _decide([unverified_lapsed]) is False

    assert caplog.text.count("source=enforcement") == 2

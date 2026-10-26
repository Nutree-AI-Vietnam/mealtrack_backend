"""Contract tests for GET /v1/me/entitlements (shared with the mobile app)."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1 import me as me_route
from src.app.handlers.query_handlers.get_my_entitlements_query_handler import (
    GetMyEntitlementsQueryHandler,
)
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.domain.model.subscription_access import SubscriptionAccessUnavailable

NOW = datetime.now(UTC)


class _EventBus:
    def __init__(self, outcome):
        self.outcome = outcome
        self.sent: list = []

    async def send(self, message):
        self.sent.append(message)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _client(event_bus) -> TestClient:
    app = FastAPI()
    app.include_router(me_route.router)
    app.dependency_overrides[get_current_user_id] = lambda: "db-user-id"
    app.dependency_overrides[get_configured_event_bus] = lambda: event_bus
    return TestClient(app)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")


def test_granted_body_has_every_contract_field_and_no_store(flag_on):
    expires = datetime(2026, 11, 22, tzinfo=UTC)
    bus = _EventBus(
        {
            "has_access": True,
            "status": "active",
            "expires_at": expires,
            "product_id": "standard_monthly",
            "will_renew": True,
            "provider": "revenuecat",
        }
    )

    response = _client(bus).get("/v1/me/entitlements")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "has_access": True,
        "status": "active",
        "expires_at": "2026-11-22T00:00:00Z",
        "product_id": "standard_monthly",
        "will_renew": True,
        "provider": "revenuecat",
    }
    assert bus.sent == [GetMyEntitlementsQuery("db-user-id")]


def test_denied_body_has_null_subscription_fields(flag_on):
    bus = _EventBus(
        {
            "has_access": False,
            "status": "none",
            "expires_at": None,
            "product_id": None,
            "will_renew": None,
            "provider": None,
        }
    )

    response = _client(bus).get("/v1/me/entitlements")

    assert response.status_code == 200
    assert response.json() == {
        "has_access": False,
        "status": "none",
        "expires_at": None,
        "product_id": None,
        "will_renew": None,
        "provider": None,
    }


def test_flag_off_answers_entitlements_disabled_without_reading(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "false")
    bus = _EventBus(AssertionError("must not be read"))

    response = _client(bus).get("/v1/me/entitlements")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "error_code": "ENTITLEMENTS_DISABLED",
            "message": "Database subscription access is turned off.",
        }
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert "retry-after" not in response.headers
    assert bus.sent == []


def test_unresolved_rows_answer_entitlements_unavailable(flag_on, monkeypatch):
    capture = MagicMock()
    monkeypatch.setattr(me_route, "capture_exception", capture)
    bus = _EventBus(SubscriptionAccessUnavailable("Subscription state is unverified"))

    response = _client(bus).get("/v1/me/entitlements")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "error_code": "ENTITLEMENTS_UNAVAILABLE",
            "message": "Subscription access cannot be decided right now.",
        }
    }
    assert response.headers["cache-control"] == "private, no-store"
    # Expected outcome for an unreconciled user: counted, not a Sentry issue.
    capture.assert_not_called()


def test_unexpected_error_is_logged_and_reported_before_the_503(
    flag_on, monkeypatch, caplog
):
    capture = MagicMock()
    monkeypatch.setattr(me_route, "capture_exception", capture)
    failure = RuntimeError("database connection reset")
    bus = _EventBus(failure)

    with caplog.at_level(logging.ERROR, logger="src.api.routes.v1.me"):
        response = _client(bus).get("/v1/me/entitlements")

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "ENTITLEMENTS_UNAVAILABLE"
    capture.assert_called_once()
    assert capture.call_args.args[0] is failure
    assert capture.call_args.kwargs["context"]["operation"] == "read"
    records = [
        record
        for record in caplog.records
        if "Entitlements could not be decided" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info[1] is failure


# --- Query handler: the policy's granting row fills the new fields ----------


@dataclass
class _Row:
    product_id: str
    provider: str | None = "revenuecat"
    status: str = "active"
    expires_at: datetime | None = None
    grace_period_expires_at: datetime | None = None
    period_type: str | None = "normal"
    provider_snapshot_at: datetime | None = NOW
    provider_sync_required_at: datetime | None = None


class _Subscriptions:
    def __init__(self, rows):
        self.rows = rows

    async def find_by_user_id(self, user_id):
        assert user_id == "db-user-id"
        return self.rows


class _Uow:
    def __init__(self, rows):
        self.subscriptions = _Subscriptions(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


async def _read(rows) -> dict:
    handler = GetMyEntitlementsQueryHandler(uow_factory=lambda: _Uow(rows))
    return await handler.handle(GetMyEntitlementsQuery("db-user-id"))


@pytest.mark.asyncio
async def test_handler_describes_the_latest_deadline_row_across_providers():
    rows = [
        _Row("standard_monthly", expires_at=NOW + timedelta(days=5)),
        _Row("momo_pass_90d", provider="momo", expires_at=NOW + timedelta(days=80)),
        _Row("standard_yearly", status="expired", expires_at=NOW - timedelta(days=1)),
    ]

    result = await _read(rows)

    assert result == {
        "has_access": True,
        "status": "active",
        "expires_at": NOW + timedelta(days=80),
        "product_id": "momo_pass_90d",
        "will_renew": False,
        "provider": "momo",
    }


@pytest.mark.asyncio
async def test_handler_lifetime_row_wins_and_does_not_renew():
    rows = [
        _Row("standard_yearly", expires_at=NOW + timedelta(days=300)),
        _Row("standard_lifetime", expires_at=None),
    ]

    result = await _read(rows)

    assert result["expires_at"] is None
    assert result["product_id"] == "standard_lifetime"
    assert result["will_renew"] is False
    assert result["provider"] == "revenuecat"


@pytest.mark.asyncio
async def test_handler_cancelled_subscription_reads_active_but_will_not_renew():
    rows = [
        _Row("standard_monthly", status="cancelled", expires_at=NOW + timedelta(days=3))
    ]

    result = await _read(rows)

    assert (result["status"], result["product_id"], result["will_renew"]) == (
        "active",
        "standard_monthly",
        False,
    )


@pytest.mark.asyncio
async def test_handler_renewing_trial_reports_will_renew():
    rows = [
        _Row(
            "standard_yearly",
            period_type="trial",
            expires_at=NOW + timedelta(days=3),
        )
    ]

    result = await _read(rows)

    assert (result["status"], result["will_renew"]) == ("trial", True)


@pytest.mark.asyncio
async def test_handler_denied_access_has_null_subscription_fields():
    rows = [_Row("standard_monthly", status="expired", expires_at=NOW)]

    result = await _read(rows)

    assert result == {
        "has_access": False,
        "status": "expired",
        "expires_at": None,
        "product_id": None,
        "will_renew": None,
        "provider": None,
    }


@pytest.mark.asyncio
async def test_handler_raises_unavailable_for_unreconciled_rows():
    with pytest.raises(SubscriptionAccessUnavailable):
        await _read([_Row("standard_monthly", provider_snapshot_at=None)])

"""GET /v1/health/subscription-access: the uptime monitor's marker alert."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import auth as auth_dep
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.health import router
from src.app.handlers.query_handlers.get_subscription_access_health_query_handler import (
    GetSubscriptionAccessHealthQueryHandler,
)
from src.app.queries.subscription_access import GetSubscriptionAccessHealthQuery
from src.app.services import subscription_access_health as health
from src.app.services.subscription_access_health import (
    AccessAlertThresholds,
    alert_thresholds_from_env,
    evaluate_marker_health,
)
from src.domain.ports.subscription_access_support_repository_port import (
    AccessMarkerHealth,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
PATH = "/v1/health/subscription-access"
TOKEN = {"X-Monitoring-Token": "monitor-secret"}


def _counts(**values) -> AccessMarkerHealth:
    facts = {
        "sync_required_users": 0,
        "stale_sync_required_users": 0,
        "refund_pending_rows": 0,
        "stale_refund_pending_rows": 0,
        "transfer_source_rows": 0,
        "oldest_marker_at": None,
    }
    return AccessMarkerHealth(**(facts | values))


class _Repository:
    def __init__(self, counts: AccessMarkerHealth | BaseException):
        self.counts = counts
        self.cutoffs: list[tuple[datetime, datetime]] = []

    async def marker_health(self, *, sync_required_before, refund_pending_before):
        self.cutoffs.append((sync_required_before, refund_pending_before))
        if isinstance(self.counts, BaseException):
            raise self.counts
        return self.counts


class _Bus:
    def __init__(self, repository):
        self.handler = GetSubscriptionAccessHealthQueryHandler(repository)
        self.sent: list = []

    async def send(self, message):
        self.sent.append(message)
        return await self.handler.handle(message)


@pytest.fixture(autouse=True)
def monitoring_token(monkeypatch):
    monkeypatch.setattr(auth_dep.settings, "MONITORING_API_TOKEN", "monitor-secret")
    for name in (
        health.ENV_SYNC_REQUIRED_MINUTES,
        health.ENV_SYNC_REQUIRED_USERS,
        health.ENV_REFUND_PENDING_MINUTES,
        health.ENV_OLDEST_MARKER_HOURS,
    ):
        monkeypatch.delenv(name, raising=False)


def _client(bus) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return TestClient(app)


@pytest.mark.parametrize("headers", [{}, {"X-Monitoring-Token": "wrong"}])
def test_needs_the_monitoring_token(headers):
    bus = _Bus(_Repository(_counts()))

    response = _client(bus).get(PATH, headers=headers)

    assert response.status_code == 403
    assert bus.sent == []


def test_is_closed_when_no_monitoring_token_is_configured(monkeypatch):
    monkeypatch.setattr(auth_dep.settings, "MONITORING_API_TOKEN", "")
    bus = _Bus(_Repository(_counts()))

    assert _client(bus).get(PATH, headers={"X-Monitoring-Token": ""}).status_code == 403


def test_answers_200_ok_with_the_counts_when_nothing_is_marked():
    bus = _Bus(_Repository(_counts()))

    response = _client(bus).get(PATH, headers=TOKEN)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "ok"
    assert body["reasons"] == []
    assert body["counts"] == {
        "sync_required_users": 0,
        "stale_sync_required_users": 0,
        "refund_pending_rows": 0,
        "stale_refund_pending_rows": 0,
        "transfer_source_rows": 0,
        "oldest_marker_age_seconds": None,
    }
    assert body["thresholds"] == {
        "sync_required_minutes": 30.0,
        "sync_required_users": 20,
        "refund_pending_minutes": 60.0,
        "oldest_marker_hours": 6.0,
    }


def test_answers_503_degraded_with_every_breached_threshold():
    marked_at = datetime.now(UTC) - timedelta(hours=7)
    counts = _counts(
        sync_required_users=30,
        stale_sync_required_users=21,
        refund_pending_rows=2,
        stale_refund_pending_rows=1,
        transfer_source_rows=1,
        oldest_marker_at=marked_at,
    )

    response = _client(_Bus(_Repository(counts))).get(PATH, headers=TOKEN)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert [reason["code"] for reason in body["reasons"]] == [
        "sync_required_backlog",
        "refund_pending_stale",
        "oldest_marker_stale",
    ]
    assert "21 users" in body["reasons"][0]["message"]
    assert body["counts"]["transfer_source_rows"] == 1
    assert 7 * 3600 - 60 <= body["counts"]["oldest_marker_age_seconds"] <= 7 * 3600 + 60


def test_a_failed_read_answers_503_without_the_exception_text(caplog):
    bus = _Bus(_Repository(RuntimeError("password=hunter2 connection refused")))

    with caplog.at_level(logging.WARNING, logger="src.api.routes.v1.health"):
        response = _client(bus).get(PATH, headers=TOKEN)

    assert response.status_code == 503
    assert response.json() == {"status": "error", "error_type": "RuntimeError"}
    assert "hunter2" not in caplog.text


def test_the_503_schema_declares_both_bodies():
    """degraded (the report) and error (the counts could not be read)."""
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()
    declared = schema["paths"][PATH]["get"]["responses"]["503"]["content"][
        "application/json"
    ]["schema"]
    refs = {option["$ref"].rsplit("/", 1)[-1] for option in declared["anyOf"]}
    error = schema["components"]["schemas"]["SubscriptionAccessHealthErrorResponse"]
    degraded = schema["components"]["schemas"]["SubscriptionAccessHealthResponse"]

    assert refs == {
        "SubscriptionAccessHealthResponse",
        "SubscriptionAccessHealthErrorResponse",
    }
    assert set(error["required"]) == {"status", "error_type"}
    assert error["properties"]["status"]["const"] == "error"
    assert degraded["properties"]["status"]["enum"] == ["ok", "degraded"]


def test_the_handler_passes_cutoffs_from_the_thresholds(monkeypatch):
    monkeypatch.setattr(
        "src.app.handlers.query_handlers.get_subscription_access_health_query_handler.utc_now",
        lambda: NOW,
    )
    monkeypatch.setenv(health.ENV_SYNC_REQUIRED_MINUTES, "45")
    monkeypatch.setenv(health.ENV_REFUND_PENDING_MINUTES, "90")
    repository = _Repository(_counts())

    import asyncio

    report = asyncio.run(
        GetSubscriptionAccessHealthQueryHandler(repository).handle(
            GetSubscriptionAccessHealthQuery()
        )
    )

    assert repository.cutoffs == [
        (NOW - timedelta(minutes=45), NOW - timedelta(minutes=90))
    ]
    assert report.checked_at == NOW


# --- Thresholds ---------------------------------------------------------------


def test_each_threshold_alerts_only_once_it_is_exceeded():
    limits = AccessAlertThresholds()
    at_limits = _counts(
        stale_sync_required_users=20,
        stale_refund_pending_rows=0,
        refund_pending_rows=3,  # pending, but none older than 60 minutes
        oldest_marker_at=NOW - timedelta(hours=6),
    )

    assert evaluate_marker_health(at_limits, limits, NOW).status == "ok"

    for counts, code in [
        (_counts(stale_sync_required_users=21), "sync_required_backlog"),
        (_counts(stale_refund_pending_rows=1), "refund_pending_stale"),
        (
            _counts(oldest_marker_at=NOW - timedelta(hours=6, seconds=1)),
            "oldest_marker_stale",
        ),
    ]:
        report = evaluate_marker_health(counts, limits, NOW)
        assert (report.status, [reason.code for reason in report.reasons]) == (
            "degraded",
            [code],
        )


def test_a_marker_newer_than_the_clock_has_no_negative_age():
    report = evaluate_marker_health(
        _counts(oldest_marker_at=NOW + timedelta(seconds=5)),
        AccessAlertThresholds(),
        NOW,
    )

    assert report.oldest_marker_age_seconds == 0


def test_thresholds_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(health.ENV_SYNC_REQUIRED_MINUTES, "15")
    monkeypatch.setenv(health.ENV_SYNC_REQUIRED_USERS, "0")
    monkeypatch.setenv(health.ENV_REFUND_PENDING_MINUTES, "120")
    monkeypatch.setenv(health.ENV_OLDEST_MARKER_HOURS, "1.5")

    assert alert_thresholds_from_env() == AccessAlertThresholds(
        sync_required_minutes=15,
        sync_required_users=0,
        refund_pending_minutes=120,
        oldest_marker_hours=1.5,
    )


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        (health.ENV_SYNC_REQUIRED_MINUTES, "0"),
        (health.ENV_SYNC_REQUIRED_MINUTES, "soon"),
        (health.ENV_SYNC_REQUIRED_USERS, "-1"),
        (health.ENV_SYNC_REQUIRED_USERS, "2.5"),
        (health.ENV_REFUND_PENDING_MINUTES, "nan"),
        (health.ENV_OLDEST_MARKER_HOURS, "inf"),
    ],
)
def test_an_invalid_threshold_keeps_its_default(monkeypatch, caplog, name, raw):
    monkeypatch.setenv(name, raw)

    with caplog.at_level(logging.WARNING, logger=health.logger.name):
        thresholds = alert_thresholds_from_env()

    assert thresholds == AccessAlertThresholds()
    assert name in caplog.text

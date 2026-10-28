"""Every Sentry rule in the runbook receives the signal it filters on.

The rules SA-1 to SA-10 (docs/runbooks/subscription-access-reconciliation.md,
"Monitoring") live in Sentry and match metric attributes, event tags and
fingerprints by name; the connector drops any attribute outside its
allowlist. Each case runs the code that emits one rule's signal through the
real Sentry connector and SDK (events into an in-memory transport, metrics
through before_send_metric) and checks that what the rule matches arrives,
and that the rule's row in the runbook names it. SA-5 is the uptime monitor
on GET /v1/health/subscription-access (tests/unit/api/
test_subscription_access_health.py).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sentry_sdk
from fastapi import HTTPException

from src import subscription_access_alerts as alerts
from src.api.dependencies import subscription_access as enforcement
from src.api.middleware.premium_check import ANSWER_REVENUECAT, RevenueCatAccess
from src.api.routes.v1 import me, webhooks
from src.domain.ports.subscription_service_port import (
    EntitlementCheck,
    RevenueCatSnapshotUnavailable,
)
from src.infra.monitoring.sentry import SentryObservabilityConnector, _before_send
from src.infra.services import subscription_access_sync_service as sync
from src.infra.services.subscription_access_refresh_service import _apply_failed
from src.observability import (
    reset_observability_connector_for_test,
    set_observability_connector_for_test,
)

ROOT = Path(__file__).resolve().parents[4]
RUNBOOK = ROOT / "docs" / "runbooks" / "subscription-access-reconciliation.md"
USER = SimpleNamespace(id="db-user-1")


class _CaptureTransport(sentry_sdk.transport.Transport):
    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@dataclass
class _Sentry:
    transport: _CaptureTransport
    metrics: list[dict[str, Any]] = field(default_factory=list)

    def metric(self, name: str) -> dict[str, Any]:
        (metric,) = [metric for metric in self.metrics if metric["name"] == name]
        return metric

    def alert_event(self, alert: str) -> dict[str, Any]:
        (event,) = [
            event
            for event in self.transport.events
            if (event.get("tags") or {}).get("alert") == alert
        ]
        return event


@pytest.fixture
def sentry():
    """A real Sentry client behind the app's connector, sending nothing out."""
    captured = _Sentry(_CaptureTransport())

    def keep_metric(metric, hint):
        captured.metrics.append(metric)
        return None

    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=captured.transport,
        default_integrations=False,
        auto_enabling_integrations=False,
        before_send=_before_send,
        enable_metrics=True,
        before_send_metric=keep_metric,
        traces_sample_rate=0,
    )
    connector = SentryObservabilityConnector()
    connector._enabled = True
    set_observability_connector_for_test(connector)
    try:
        yield captured
    finally:
        sentry_sdk.flush(timeout=2)
        sentry_sdk.get_client().close(timeout=2)
        sentry_sdk.init(dsn="")
        reset_observability_connector_for_test()


def _runbook_row(rule: str) -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    monitoring = text[text.index("## Monitoring") :]
    (row,) = [line for line in monitoring.splitlines() if line.startswith(f"| {rule} ")]
    return row


# --- Emitters: the code that sends each rule's signal ---------------------------


def _snapshot_unavailable(reason: str, source: str = "webhook") -> Callable[..., None]:
    def emit(monkeypatch) -> None:
        sync.record_snapshot_unavailable(
            RevenueCatSnapshotUnavailable("RevenueCat failed", reason=reason),
            source=source,
            user_id=USER.id,
            event_type="RENEWAL",
            event_id="evt-1",
        )

    return emit


def _sync_outcome(**outcome: Any) -> Callable[..., None]:
    def emit(monkeypatch) -> None:
        sync._record_sync_outcome(
            sync.SubscriptionSyncOutcome(**outcome),
            USER,
            "webhook",
            "RENEWAL",
            "evt-1",
            False,
        )

    return emit


def _apply_failure(monkeypatch) -> None:
    _apply_failed(RuntimeError("database down"), source="support_resync", user_id="u")


def _webhook_failure(monkeypatch) -> None:
    webhooks._report_processing_error(
        RuntimeError("database down"), {"type": "RENEWAL", "id": "evt-1"}
    )


def _entitlements_failure(monkeypatch) -> None:
    me._report_unexpected(RuntimeError("bug"), user_id=USER.id, operation="read")


def _enforcement_refusal(monkeypatch) -> None:
    class _Bus:
        async def send(self, query):
            return False  # the database grants nothing

    async def revenuecat_down(user_id):
        return RevenueCatAccess(EntitlementCheck.UNAVAILABLE, ANSWER_REVENUECAT)

    monkeypatch.setattr(enforcement, "subscription_enforcement_enabled", lambda: True)
    monkeypatch.setattr(enforcement, "_event_bus", lambda: _Bus())
    monkeypatch.setattr(enforcement, "_revenuecat_access", revenuecat_down)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(enforcement.require_active_subscription(user_id=USER.id))
    assert refused.value.status_code == 402


# --- Metric alerts --------------------------------------------------------------

METRIC_RULES = [
    # (rule, emitter, metric, attribute the rule filters on, its value)
    *(
        (
            "SA-6",
            _snapshot_unavailable(kind),
            "subscription_access.snapshot_unavailable",
            "failure_kind",
            kind,
        )
        for kind in ("transport", "http_429", "http_5xx")
    ),
    (
        "SA-7",
        _sync_outcome(
            result="applied",
            access_before="granted",
            access_after="denied",
            rows_revoked=1,
        ),
        "subscription_access.downgrade",
        "expected",
        "false",
    ),
    (
        "SA-8",
        _entitlements_failure,
        "subscription_access.entitlements_unavailable",
        "failure_kind",
        "unexpected",
    ),
    (
        "SA-9",
        _sync_outcome(result="applied", access_before="denied", access_after="granted"),
        "subscription_access.sync",
        "source",
        "webhook",
    ),
    (
        "SA-10",
        _enforcement_refusal,
        "subscription_access.enforcement_denied",
        "revenuecat",
        "unavailable",
    ),
    (
        "SA-3",
        _webhook_failure,
        "webhook.revenuecat.processed",
        "status",
        "error",
    ),
]


@pytest.mark.parametrize(
    ("rule", "emit", "metric", "attribute", "value"),
    METRIC_RULES,
    ids=[f"{case[0]}-{case[2]}-{case[4]}" for case in METRIC_RULES],
)
def test_metric_rule_attributes_reach_sentry(
    sentry, monkeypatch, rule, emit, metric, attribute, value
):
    with sentry_sdk.isolation_scope():
        emit(monkeypatch)

    sent = sentry.metric(metric)
    assert sent["type"] == "counter"
    assert sent["attributes"].get(attribute) == value, sent["attributes"]
    row = _runbook_row(rule)
    assert f"`{metric}`" in row
    assert attribute in row and value in row


def test_transient_snapshot_failures_alert_by_their_counter_only(sentry, monkeypatch):
    """SA-6 counts them; SA-1 never sees them (one event per webhook during
    a RevenueCat outage would flood Sentry)."""
    with sentry_sdk.isolation_scope():
        _snapshot_unavailable("http_5xx")(monkeypatch)

    assert sentry.transport.events == []


# --- Issue alerts ---------------------------------------------------------------

ISSUE_RULES = [
    # (rule, emitter, alert key, fingerprint parts after the key, other tags)
    (
        "SA-1",
        _snapshot_unavailable("malformed", source="support_resync"),
        alerts.ALERT_SNAPSHOT_FAILURE,
        ["malformed"],
        {"failure_kind": "malformed", "source": "support_resync"},
    ),
    (
        "SA-2",
        _apply_failure,
        alerts.ALERT_SYNC_APPLY_ERROR,
        ["support_resync"],
        {"source": "support_resync"},
    ),
    (
        "SA-3",
        _webhook_failure,
        alerts.ALERT_WEBHOOK_PROCESSING_ERROR,
        ["RuntimeError"],
        {"event_type": "RENEWAL"},
    ),
    (
        "SA-4",
        _sync_outcome(
            result="applied",
            access_before="granted",
            access_after="granted",
            standard_missing=True,
        ),
        alerts.ALERT_STANDARD_MISSING,
        [],
        {"source": "webhook"},
    ),
]


@pytest.mark.parametrize(
    ("rule", "emit", "alert", "parts", "tags"),
    ISSUE_RULES,
    ids=[case[0] for case in ISSUE_RULES],
)
def test_issue_rule_tag_and_fingerprint_reach_sentry(
    sentry, monkeypatch, rule, emit, alert, parts, tags
):
    with sentry_sdk.isolation_scope():
        emit(monkeypatch)

    event = sentry.alert_event(alert)
    assert event["fingerprint"] == [alert, *parts]
    for tag, value in tags.items():
        assert event["tags"].get(tag) == value, event["tags"]
    assert f"`{alert}`" in _runbook_row(rule)


def test_every_runbook_rule_but_the_uptime_monitor_is_covered():
    covered = {case[0] for case in METRIC_RULES} | {case[0] for case in ISSUE_RULES}

    assert covered | {"SA-5"} == {f"SA-{number}" for number in range(1, 11)}
    assert "Uptime monitor" in _runbook_row("SA-5")

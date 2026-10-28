"""The alert keys and thresholds the runbook's rules are built on.

The rules live in Sentry and the uptime monitor, not in this repository, so
renaming a key or a variable silently breaks them; these tests make the
rename touch the runbook too.
"""

from pathlib import Path

import pytest

from src import subscription_access_alerts as alerts
from src.app.services import subscription_access_health as health

ROOT = Path(__file__).resolve().parents[4]
RUNBOOK = ROOT / "docs" / "runbooks" / "subscription-access-reconciliation.md"
ALERT_KEYS = [
    alerts.ALERT_SNAPSHOT_FAILURE,
    alerts.ALERT_SYNC_APPLY_ERROR,
    alerts.ALERT_WEBHOOK_PROCESSING_ERROR,
    alerts.ALERT_STANDARD_MISSING,
    alerts.ALERT_UNEXPECTED_DOWNGRADE,
]
THRESHOLD_VARIABLES = [
    health.ENV_SYNC_REQUIRED_MINUTES,
    health.ENV_SYNC_REQUIRED_USERS,
    health.ENV_REFUND_PENDING_MINUTES,
    health.ENV_OLDEST_MARKER_HOURS,
]


def _monitoring_section() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    return text[text.index("## Monitoring") :]


@pytest.mark.parametrize("key", ALERT_KEYS)
def test_every_alert_key_has_a_rule_in_the_runbook(key):
    assert f"`{key}`" in _monitoring_section()


@pytest.mark.parametrize("name", THRESHOLD_VARIABLES)
def test_every_threshold_is_documented_and_in_the_env_example(name):
    assert name in _monitoring_section()
    assert f"{name}=" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_the_runbook_says_the_rules_are_not_in_the_repository():
    assert "no alerting-as-code" in _monitoring_section()


@pytest.mark.parametrize("rule", ["SA-1", "SA-2", "SA-3", "SA-4"])
def test_paging_issue_rules_fire_on_every_recurrence(rule):
    """The fixed fingerprint keeps one issue per key and kind, so "A new
    issue is created" fires once ever: the paging rules trigger on any event
    and the issue is resolved once fixed."""
    monitoring = _monitoring_section()
    (row,) = [line for line in monitoring.splitlines() if line.startswith(f"| {rule} ")]
    prose = " ".join(monitoring.split())

    assert "Sentry issue alert" in row and "| Page |" in row
    assert "Resolve the issue once fixed" in row
    assert (
        'the paging rules SA-1 to SA-4 use the trigger "The issue is seen more '
        'than 0 times in 5 minutes"' in prose
    )
    assert 'use the triggers "A new issue is created"' not in prose


def test_fingerprints_are_the_key_then_bounded_parts():
    assert alerts.alert_fingerprint(alerts.ALERT_SNAPSHOT_FAILURE, "malformed") == [
        "subscription_access.snapshot_failure",
        "malformed",
    ]
    assert alerts.alert_fingerprint(alerts.ALERT_STANDARD_MISSING) == [
        "subscription_access.standard_missing"
    ]
    assert alerts.alert_fingerprint(alerts.ALERT_SYNC_APPLY_ERROR, None) == [
        "subscription_access.sync_apply_error",
        "none",
    ]


def test_every_doc_gives_the_uptime_monitor_the_same_poll_interval():
    """SA-5 sets it; the endpoint's docs and the index's reason state it."""
    (row,) = [
        line
        for line in _monitoring_section().splitlines()
        if line.startswith("| SA-5 ")
    ]
    endpoints = (ROOT / "docs" / "api-endpoints.md").read_text(encoding="utf-8")
    monitoring = endpoints[endpoints.index("### Subscription access monitoring") :]
    stated = {
        "runbook SA-5": row,
        "api-endpoints.md": monitoring[: monitoring.index("\n### ")],
        "health query handler": (
            ROOT
            / "src/app/handlers/query_handlers"
            / "get_subscription_access_health_query_handler.py"
        ).read_text(encoding="utf-8"),
        "subscription model": (
            ROOT / "src/infra/database/models/subscription.py"
        ).read_text(encoding="utf-8"),
        "index migration": (
            ROOT
            / "migrations/versions"
            / "20261028142458697212_add_subscription_access_marker_index.py"
        ).read_text(encoding="utf-8"),
    }

    for where, text in stated.items():
        prose = " ".join(text.split())
        assert "every 5 minutes" in prose, where
        assert "every minute" not in prose, where

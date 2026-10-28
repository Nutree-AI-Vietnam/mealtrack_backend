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

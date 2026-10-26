"""Tests for normalizing verified RevenueCat subscription facts."""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable
from src.infra.services.subscription_access_sync_service import (
    _platform,
    normalize_standard_access_facts,
)


def test_billing_issue_keeps_grace_fact_after_paid_expiry():
    now = datetime(2026, 10, 22, tzinfo=UTC)
    subscriber = {
        "entitlements": {
            "standard": {
                "product_identifier": "web_monthly",
                "expires_date": (now - timedelta(days=1)).isoformat(),
            }
        },
        "subscriptions": {
            "web_monthly": {
                "purchase_date": (now - timedelta(days=30)).isoformat(),
                "store": "PADDLE",
                "is_sandbox": False,
                "billing_issues_detected_at": (now - timedelta(days=2)).isoformat(),
                "grace_period_expires_date": (now + timedelta(days=2)).isoformat(),
            }
        },
    }

    facts = normalize_standard_access_facts(subscriber, now)

    assert facts is not None
    assert facts["status"] == "billing_issue"
    assert facts["platform"] == "web"
    assert facts["grace_period_expires_at"] == now + timedelta(days=2)


def test_paddle_store_is_supported():
    assert _platform("PADDLE") == "web"


def test_malformed_expiry_is_not_treated_as_lifetime_access():
    subscriber = {
        "entitlements": {"standard": {"product_identifier": "web_monthly"}},
        "subscriptions": {
            "web_monthly": {
                "purchase_date": "2026-10-01T00:00:00Z",
                "store": "STRIPE",
                "is_sandbox": False,
            }
        },
    }

    with pytest.raises(RevenueCatSnapshotUnavailable):
        normalize_standard_access_facts(subscriber)

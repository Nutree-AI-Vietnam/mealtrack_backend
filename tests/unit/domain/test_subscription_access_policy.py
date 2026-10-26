"""Boundary tests for normalized database access decisions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)


@dataclass
class Facts:
    status: str = "active"
    expires_at: datetime | None = None
    grace_period_expires_at: datetime | None = None
    period_type: str | None = None
    provider_snapshot_at: datetime | None = datetime(2026, 1, 1, tzinfo=UTC)
    provider_sync_required_at: datetime | None = None


def test_active_paid_through_cancelled_and_trial_are_accessible():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    trial = Facts(expires_at=now + timedelta(days=5), period_type="trial")
    result = evaluate_subscription_access([trial], now)
    assert (result.has_access, result.status, result.expires_at) == (
        True,
        "trial",
        now + timedelta(days=5),
    )

    cancelled = Facts(status="cancelled", expires_at=now + timedelta(days=2))
    assert evaluate_subscription_access([cancelled], now).status == "active"


def test_grace_boundary_and_expiry_equality_deny_access():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    billing_issue = Facts(
        status="billing_issue", grace_period_expires_at=now + timedelta(seconds=1)
    )
    assert evaluate_subscription_access([billing_issue], now).status == "grace_period"
    assert not evaluate_subscription_access([Facts(expires_at=now)], now).has_access


def test_billing_issue_grants_grace_after_paid_expiry():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    billing_issue = Facts(
        status="billing_issue",
        expires_at=now - timedelta(seconds=1),
        grace_period_expires_at=now + timedelta(seconds=1),
    )
    access = evaluate_subscription_access([billing_issue], now)
    assert access.has_access is True
    assert access.status == "grace_period"


def test_verified_lifetime_and_multiple_rows_are_aggregated_safely():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        Facts(status="expired", expires_at=now + timedelta(days=30)),
        Facts(expires_at=None),
    ]
    result = evaluate_subscription_access(rows, now)
    assert result.has_access is True
    assert result.expires_at is None


def test_legacy_active_rows_are_unavailable_until_verified():
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([Facts(provider_snapshot_at=None)])


def test_refund_and_expired_status_do_not_grant_from_future_expiry():
    future = datetime.now(UTC) + timedelta(days=10)
    for status in ("refunded", "expired"):
        result = evaluate_subscription_access([Facts(status=status, expires_at=future)])
        assert result.has_access is False
        assert result.status == "expired"


@pytest.mark.parametrize("status", ["active", "expired", "refunded"])
def test_row_awaiting_a_provider_sync_is_unresolved_whatever_its_status(status):
    """An event applied without its snapshot must not read as a decision."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pending = Facts(
        status=status,
        expires_at=now + timedelta(days=30),
        provider_sync_required_at=now,
    )

    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([pending], now)


def test_verified_grant_from_another_row_still_wins_over_a_pending_row():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pending = Facts(status="expired", provider_sync_required_at=now)
    verified = Facts(expires_at=now + timedelta(days=3))

    assert evaluate_subscription_access([pending, verified], now).has_access is True

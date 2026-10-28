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


@dataclass
class Row(Facts):
    product_id: str = "standard_monthly"
    provider: str | None = "revenuecat"


def test_source_is_the_grant_with_the_latest_deadline():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    short = Row(product_id="standard_monthly", expires_at=now + timedelta(days=3))
    long = Row(product_id="standard_yearly", expires_at=now + timedelta(days=300))
    denied = Row(product_id="old", status="expired", expires_at=now)

    access = evaluate_subscription_access([short, denied, long], now)

    assert access.source is long
    assert access.expires_at == long.expires_at


def test_lifetime_grant_is_the_source_over_any_dated_grant():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    dated = Row(product_id="standard_yearly", expires_at=now + timedelta(days=300))
    lifetime = Row(product_id="standard_lifetime", expires_at=None)

    access = evaluate_subscription_access([dated, lifetime], now)

    assert access.source is lifetime
    assert access.expires_at is None


def test_source_crosses_providers_and_covers_grace_grants():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store_grace = Row(
        status="billing_issue",
        expires_at=now - timedelta(days=1),
        grace_period_expires_at=now + timedelta(days=2),
    )
    pass_row = Row(
        product_id="momo_pass_30d",
        provider="momo",
        expires_at=now + timedelta(days=20),
    )

    access = evaluate_subscription_access([store_grace, pass_row], now)

    assert access.source is pass_row
    assert access.status == "active"


def test_denied_and_empty_decisions_have_no_source():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert evaluate_subscription_access([], now).source is None
    expired = Row(status="expired", expires_at=now - timedelta(days=1))
    assert evaluate_subscription_access([expired], now).source is None


def test_source_does_not_change_decision_equality():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = evaluate_subscription_access([Row(expires_at=now + timedelta(days=1))], now)
    second = evaluate_subscription_access(
        [Row(product_id="other", expires_at=now + timedelta(days=1))], now
    )
    assert first == second


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (Row(status="active", expires_at=datetime(2027, 1, 1, tzinfo=UTC)), True),
        (Row(status="cancelled", expires_at=datetime(2027, 1, 1, tzinfo=UTC)), False),
        (
            Row(status="billing_issue", expires_at=datetime(2027, 1, 1, tzinfo=UTC)),
            False,
        ),
        (Row(status="active", expires_at=None), False),
        (
            Row(
                status="active",
                provider="momo",
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            ),
            False,
        ),
        (
            Row(
                status="active",
                provider=None,
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            ),
            False,
        ),
    ],
)
def test_will_renew_mirrors_revenuecat_and_is_false_for_non_renewing(row, expected):
    from src.domain.model.subscription_access import subscription_will_renew

    assert subscription_will_renew(row) is expected


@dataclass
class MarkedFacts(Facts):
    provider_refund_pending_at: datetime | None = None
    provider_transfer_source_at: datetime | None = None


def test_unresolved_reasons_name_each_unverified_row_kind_once_in_order():
    from src.domain.model.subscription_access import unresolved_access_reasons

    now = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        MarkedFacts(status="cancelled", provider_snapshot_at=None),
        MarkedFacts(
            status="expired",
            provider_sync_required_at=now,
            provider_transfer_source_at=now,
        ),
        MarkedFacts(provider_sync_required_at=now, provider_refund_pending_at=now),
        MarkedFacts(status="cancelled", provider_snapshot_at=None),
    ]

    assert unresolved_access_reasons(rows) == [
        "sync_required",
        "refund_pending",
        "transfer_source",
        "unverified_grant",
    ]


def test_unresolved_reasons_are_empty_when_every_row_is_verified():
    from src.domain.model.subscription_access import unresolved_access_reasons

    rows = [
        MarkedFacts(),
        # Never granting, so an unverified expired row decides nothing.
        MarkedFacts(status="expired", provider_snapshot_at=None),
        # A refund marker without the sync marker does not stop the policy.
        MarkedFacts(provider_refund_pending_at=datetime(2026, 1, 1, tzinfo=UTC)),
    ]

    assert unresolved_access_reasons(rows) == []

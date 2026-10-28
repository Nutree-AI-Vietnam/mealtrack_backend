"""Read models behind the subscription access support API."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from src.app.services.subscription_access_support import (
    AccessDecision,
    InvalidQueueCursor,
    current_access_flags,
    decode_queue_cursor,
    describe_access_decision,
    encode_queue_cursor,
)
from src.domain.ports.subscription_access_support_repository_port import (
    UnresolvedQueueCursor,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@dataclass
class _Row:
    status: str = "active"
    product_id: str = "standard_monthly"
    provider: str = "revenuecat"
    expires_at: datetime | None = NOW + timedelta(days=20)
    grace_period_expires_at: datetime | None = None
    period_type: str | None = "normal"
    provider_snapshot_at: datetime | None = NOW - timedelta(days=1)
    provider_sync_required_at: datetime | None = None
    provider_refund_pending_at: datetime | None = None
    provider_transfer_source_at: datetime | None = None


def test_a_granted_decision_matches_the_entitlements_body():
    decision = describe_access_decision([_Row()], now=NOW)

    assert decision == AccessDecision(
        "granted",
        has_access=True,
        status="active",
        expires_at=NOW + timedelta(days=20),
        product_id="standard_monthly",
        will_renew=True,
        provider="revenuecat",
    )


@pytest.mark.parametrize(
    ("rows", "status"), [([], "none"), ([_Row(status="expired")], "expired")]
)
def test_a_denied_decision_has_no_subscription_fields(rows, status):
    decision = describe_access_decision(rows, now=NOW)

    assert decision == AccessDecision("denied", has_access=False, status=status)


def test_an_unresolved_decision_says_which_rows_the_policy_could_not_verify():
    rows = [
        _Row(status="cancelled", provider_snapshot_at=None),
        _Row(status="expired", provider_sync_required_at=NOW),
    ]

    decision = describe_access_decision(rows, now=NOW)

    assert decision == AccessDecision(
        "unresolved", unresolved_reasons=["sync_required", "unverified_grant"]
    )


def test_a_verified_grant_wins_over_an_unverified_row():
    rows = [_Row(), _Row(status="cancelled", provider_sync_required_at=NOW)]

    assert describe_access_decision(rows, now=NOW).outcome == "granted"


def test_flags_report_the_raw_enforcement_flag_and_whether_it_is_effective(
    monkeypatch,
):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "false")
    monkeypatch.setenv("SUBSCRIPTION_ENFORCEMENT_ENABLED", "true")

    flags = current_access_flags()

    assert flags.subscription_access_enabled is False
    assert flags.subscription_enforcement_enabled is True
    assert flags.enforcement_active is False


def test_queue_cursor_round_trips_to_the_microsecond():
    cursor = UnresolvedQueueCursor(
        NOW.replace(microsecond=123456), "6f1c7c1e-0000-4000-8000-000000000001"
    )

    encoded = encode_queue_cursor(cursor)

    assert "=" not in encoded and "/" not in encoded and "+" not in encoded
    assert decode_queue_cursor(encoded) == cursor


@pytest.mark.parametrize(
    "value", ["", "%%%", "bm90IGpzb24", "WzEsIDJd", "WyJ4IiwgInkiXQ"]
)
def test_queue_cursor_rejects_anything_it_did_not_encode(value):
    with pytest.raises(InvalidQueueCursor):
        decode_queue_cursor(value)

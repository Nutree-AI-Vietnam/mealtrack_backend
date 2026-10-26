"""Tests for normalizing verified RevenueCat subscription facts."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.fixtures.revenuecat_samples import (
    standard_annual_response,
    verbatim_subscriber_response,
)

from src.domain.ports.subscription_service_port import (
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.services.subscription_access_sync_service import (
    _platform,
    normalize_standard_access_facts,
    sync_revenuecat_access,
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


@pytest.mark.parametrize(
    ("product", "expected"),
    [("onemonth", "1000000652379790"), ("annual", "GPA.6801-7988-0152-76034..5")],
)
def test_store_transaction_id_is_read_from_revenuecat_field(product, expected):
    """v1 names it store_transaction_id; App Store ids arrive as numbers."""
    subscriber = verbatim_subscriber_response()["subscriber"]
    subscriber["entitlements"] = {
        "standard": {
            "expires_date": "2099-01-01T00:00:00Z",
            "grace_period_expires_date": None,
            "product_identifier": product,
            "purchase_date": "2019-07-14T20:07:40Z",
        }
    }

    facts = normalize_standard_access_facts(
        subscriber, datetime(2019, 7, 26, tzinfo=UTC)
    )

    assert facts is not None
    assert facts["store_transaction_id"] == expected


def test_missing_store_transaction_id_normalizes_to_none():
    now = datetime(2026, 10, 26, tzinfo=UTC)
    body = standard_annual_response(
        request_date=now,
        expires_date=now + timedelta(days=30),
        store_transaction_id=None,
    )

    facts = normalize_standard_access_facts(body["subscriber"], now)

    assert facts is not None
    assert facts["store_transaction_id"] is None


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _SyncSession:
    """Session double for sync_revenuecat_access: one owner, its rows."""

    def __init__(self, user, rows):
        self.user = user
        self.rows = rows
        self.added = []

    async def scalar(self, _statement):
        return self.user

    async def scalars(self, _statement):
        return _Rows(list(self.rows))

    def add(self, row):
        self.added.append(row)
        self.rows.append(row)

    async def flush(self):
        return None


def _owner():
    return User(
        id="user-1",
        firebase_uid="firebase-uid",
        updated_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _revenuecat_row(**values):
    return Subscription(
        **{
            "id": "row-1",
            "user_id": "user-1",
            "revenuecat_subscriber_id": "firebase-uid",
            "provider": "revenuecat",
            "product_id": "annual",
            "platform": "ios",
            "status": "active",
            "purchased_at": datetime(2025, 10, 26, tzinfo=UTC),
            "is_sandbox": False,
        }
        | values
    )


@pytest.mark.asyncio
async def test_sync_keeps_stored_transaction_id_when_snapshot_has_none():
    now = datetime.now(UTC)
    body = standard_annual_response(
        request_date=now,
        expires_date=now + timedelta(days=30),
        store_transaction_id=None,
    )
    row = _revenuecat_row(store_transaction_id="1000000000000001")
    user = _owner()

    await sync_revenuecat_access(
        _SyncSession(user, [row]),
        user,
        snapshot=RevenueCatSnapshot(
            body["subscriber"], now, app_user_id="firebase-uid"
        ),
    )

    assert row.store_transaction_id == "1000000000000001"
    assert row.status == "active"
    assert row.provider_snapshot_at == now

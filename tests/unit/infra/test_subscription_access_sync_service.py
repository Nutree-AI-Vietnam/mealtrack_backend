"""Tests for normalizing verified RevenueCat subscription facts."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql
from tests.fixtures.revenuecat_samples import (
    empty_customer_response,
    patched_revenuecat_http,
    standard_annual_response,
    verbatim_subscriber_response,
)

from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.ports.subscription_service_port import (
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.services.subscription_access_sync_service import (
    _platform,
    mark_revenuecat_access_unresolved,
    normalize_standard_access_facts,
    record_snapshot_unavailable,
    sync_revenuecat_access,
)

SYNC = "src.infra.services.subscription_access_sync_service"


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
        self.statements = []
        self.flushes = 0

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.user

    async def scalars(self, statement):
        self.statements.append(statement)
        return _Rows(list(self.rows))

    def add(self, row):
        self.added.append(row)
        self.rows.append(row)

    async def flush(self):
        self.flushes += 1


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


def _granting_row(now, **values):
    return _revenuecat_row(
        **{
            "expires_at": now + timedelta(days=30),
            "provider_snapshot_at": now - timedelta(days=1),
            "period_type": "normal",
        }
        | values
    )


def _snapshot(body, request_date, app_user_id="firebase-uid", absent=False):
    return RevenueCatSnapshot(
        body["subscriber"], request_date, absent=absent, app_user_id=app_user_id
    )


@pytest.mark.asyncio
async def test_sync_serialises_on_a_row_lock_not_on_users_updated_at():
    """A users-row write between lookup and sync no longer aborts the sync."""
    now = datetime.now(UTC)
    caller_copy = _owner()
    locked = _owner()
    locked.updated_at = caller_copy.updated_at + timedelta(seconds=3)
    session = _SyncSession(locked, [])
    body = standard_annual_response(
        request_date=now, expires_date=now + timedelta(days=30)
    )

    outcome = await sync_revenuecat_access(
        session, caller_copy, snapshot=_snapshot(body, now)
    )

    assert outcome.result == "applied"
    assert outcome.row_created is True
    lock_sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "FROM users" in lock_sql and "FOR UPDATE" in lock_sql
    rows_sql = str(
        session.statements[1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "subscriptions.provider = 'revenuecat'" in rows_sql
    assert "FOR UPDATE" in rows_sql
    assert locked.updated_at == caller_copy.updated_at + timedelta(seconds=3)


@pytest.mark.asyncio
async def test_sync_refuses_a_snapshot_read_for_another_firebase_uid():
    now = datetime.now(UTC)
    row = _granting_row(now)
    body = standard_annual_response(request_date=now, expires_date=now)

    with pytest.raises(RevenueCatSnapshotUnavailable) as error:
        await sync_revenuecat_access(
            _SyncSession(_owner(), [row]),
            _owner(),
            snapshot=_snapshot(body, now, app_user_id="someone-else"),
        )

    assert error.value.reason == "owner_changed"
    assert row.status == "active"
    assert row.provider_snapshot_at == now - timedelta(days=1)


@pytest.mark.asyncio
async def test_older_snapshot_never_overwrites_newer_facts():
    now = datetime.now(UTC)
    row = _granting_row(now, provider_snapshot_at=now)
    body = standard_annual_response(
        request_date=now - timedelta(minutes=1),
        expires_date=now - timedelta(days=1),
    )

    with (
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        outcome = await sync_revenuecat_access(
            _SyncSession(_owner(), [row]),
            _owner(),
            snapshot=_snapshot(body, now - timedelta(minutes=1)),
            source="webhook",
            event_type="RENEWAL",
        )

    assert outcome.result == "stale_skipped"
    assert (row.status, row.provider_snapshot_at) == ("active", now)
    metric.assert_called_once_with(
        "subscription_access.sync",
        attributes={
            "source": "webhook",
            "event_type": "RENEWAL",
            "result": "stale_skipped",
        },
    )
    # Routine (a refresh that commits before the purchase webhook): counted
    # and logged, never one Sentry event per occurrence.
    captured.assert_not_called()


@pytest.mark.asyncio
async def test_absence_never_expires_a_possible_grant():
    now = datetime.now(UTC)
    row = _granting_row(now)

    with pytest.raises(RevenueCatSnapshotUnavailable) as error:
        await sync_revenuecat_access(
            _SyncSession(_owner(), [row]),
            _owner(),
            snapshot=_snapshot(empty_customer_response(), now, absent=True),
        )

    assert error.value.reason == "absent_with_history"
    assert row.status == "active"


@pytest.mark.asyncio
async def test_absence_with_only_terminal_rows_resolves_to_no_access():
    now = datetime.now(UTC)
    row = _revenuecat_row(
        status="expired", provider_sync_required_at=now - timedelta(minutes=5)
    )

    outcome = await sync_revenuecat_access(
        _SyncSession(_owner(), [row]),
        _owner(),
        snapshot=_snapshot(empty_customer_response(), now, absent=True),
    )

    assert outcome.result == "absent"
    assert outcome.access_after == "denied"
    assert row.provider_sync_required_at is None


@pytest.mark.asyncio
async def test_absence_resolves_a_long_expired_unverified_row_to_no_access():
    """An unreconciled 'cancelled' row that lapsed in 2025 can never grant.
    Without this, an absent customer left the user unresolved for good: GET
    and refresh kept answering 503 and the cutover gate stayed NO-GO."""
    now = datetime.now(UTC)
    row = _revenuecat_row(
        status="cancelled",
        expires_at=datetime(2025, 1, 1, tzinfo=UTC),
        provider_snapshot_at=None,
    )
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([row], now)

    outcome = await sync_revenuecat_access(
        _SyncSession(_owner(), [row]),
        _owner(),
        snapshot=_snapshot(empty_customer_response(), now, absent=True),
        source="refresh",
    )

    assert outcome.result == "absent"
    assert (outcome.access_before, outcome.access_after) == ("unresolved", "denied")
    assert (row.status, row.provider_snapshot_at) == ("expired", now)
    assert evaluate_subscription_access([row], now).has_access is False


@pytest.mark.asyncio
async def test_absence_never_expires_a_row_still_in_its_grace_period():
    now = datetime.now(UTC)
    row = _revenuecat_row(
        status="billing_issue",
        expires_at=now - timedelta(days=1),
        grace_period_expires_at=now + timedelta(days=2),
        provider_snapshot_at=None,
    )

    with pytest.raises(RevenueCatSnapshotUnavailable) as error:
        await sync_revenuecat_access(
            _SyncSession(_owner(), [row]),
            _owner(),
            snapshot=_snapshot(empty_customer_response(), now, absent=True),
        )

    assert error.value.reason == "absent_with_history"
    assert (row.status, row.provider_snapshot_at) == ("billing_issue", None)


@pytest.mark.asyncio
async def test_an_older_absent_snapshot_never_moves_verified_facts_back():
    now = datetime.now(UTC)
    row = _revenuecat_row(
        status="cancelled",
        expires_at=now - timedelta(days=3),
        provider_snapshot_at=now,
    )

    await sync_revenuecat_access(
        _SyncSession(_owner(), [row]),
        _owner(),
        snapshot=_snapshot(
            empty_customer_response(), now - timedelta(minutes=1), absent=True
        ),
    )

    assert (row.status, row.provider_snapshot_at) == ("cancelled", now)


@pytest.mark.asyncio
async def test_sync_marker_is_cleared_only_by_a_snapshot_taken_after_it():
    now = datetime.now(UTC)
    marker = now - timedelta(seconds=10)
    row = _revenuecat_row(status="active", provider_sync_required_at=marker)
    session = _SyncSession(_owner(), [row])
    older = standard_annual_response(
        request_date=marker - timedelta(seconds=1),
        expires_date=now + timedelta(days=30),
    )

    first = await sync_revenuecat_access(
        session, _owner(), snapshot=_snapshot(older, marker - timedelta(seconds=1))
    )

    assert first.access_after == "unresolved"
    assert row.provider_sync_required_at == marker

    newer = standard_annual_response(
        request_date=now, expires_date=now + timedelta(days=30)
    )
    second = await sync_revenuecat_access(
        session, _owner(), snapshot=_snapshot(newer, now)
    )

    assert second.access_after == "granted"
    assert row.provider_sync_required_at is None
    assert row.provider_snapshot_at == now


def _several_revenuecat_rows(now):
    return [
        _granting_row(now, id="row-annual"),
        # Granting under another product: the snapshot's apply revokes it.
        _granting_row(now, id="row-monthly", product_id="onemonth"),
        _revenuecat_row(
            id="row-weekly",
            product_id="oneweek",
            status="expired",
            expires_at=now - timedelta(days=200),
            provider_snapshot_at=now - timedelta(days=200),
        ),
    ]


@pytest.mark.asyncio
async def test_the_same_read_applied_twice_is_unchanged_for_several_rows():
    """Two deliveries can apply one RevenueCat read (they queue on the lock;
    RevenueCat can serve the same read twice). The first apply stamps every
    row; the second must recognise that, not call it conflicting facts."""
    now = datetime.now(UTC).replace(microsecond=0)
    rows = _several_revenuecat_rows(now)
    session = _SyncSession(_owner(), rows)
    body = standard_annual_response(
        request_date=now, expires_date=now + timedelta(days=365)
    )

    first = await sync_revenuecat_access(
        session, _owner(), snapshot=_snapshot(body, now)
    )
    second = await sync_revenuecat_access(
        session, _owner(), snapshot=_snapshot(body, now)
    )

    assert (first.result, first.rows_revoked) == ("applied", 1)
    assert (second.result, second.access_after) == ("unchanged", "granted")
    assert [row.status for row in rows] == ["active", "expired", "expired"]
    assert evaluate_subscription_access(rows).has_access is True


@pytest.mark.asyncio
async def test_the_same_no_access_read_applied_twice_is_unchanged():
    now = datetime.now(UTC).replace(microsecond=0)
    rows = _several_revenuecat_rows(now)
    session = _SyncSession(_owner(), rows)
    body = standard_annual_response(
        request_date=now, expires_date=now - timedelta(days=1)
    )

    await sync_revenuecat_access(
        session, _owner(), snapshot=_snapshot(body, now), refund_event=True
    )
    second = await sync_revenuecat_access(
        session, _owner(), snapshot=_snapshot(body, now)
    )

    assert (second.result, second.access_after) == ("unchanged", "denied")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_expiry", [timedelta(days=30), timedelta(days=-1)], ids=["grant", "none"]
)
async def test_another_read_at_the_same_time_is_refused_as_conflicting(
    other_expiry,
):
    now = datetime.now(UTC).replace(microsecond=0)
    rows = _several_revenuecat_rows(now)
    session = _SyncSession(_owner(), rows)
    applied = standard_annual_response(
        request_date=now, expires_date=now + timedelta(days=365)
    )
    other = standard_annual_response(request_date=now, expires_date=now + other_expiry)
    await sync_revenuecat_access(session, _owner(), snapshot=_snapshot(applied, now))

    with pytest.raises(RevenueCatSnapshotUnavailable) as error:
        await sync_revenuecat_access(session, _owner(), snapshot=_snapshot(other, now))

    assert error.value.reason == "conflicting_facts"
    assert rows[0].expires_at == now + timedelta(days=365)


@pytest.mark.asyncio
async def test_unexpected_downgrade_and_missing_standard_are_reported():
    now = datetime.now(UTC)
    row = _granting_row(now)
    body = standard_annual_response(
        request_date=now, expires_date=now + timedelta(days=30)
    )
    body["subscriber"]["entitlements"] = {
        "premium": body["subscriber"]["entitlements"]["standard"]
    }

    with (
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        outcome = await sync_revenuecat_access(
            _SyncSession(_owner(), [row]),
            _owner(),
            snapshot=_snapshot(body, now),
            source="webhook",
            event_type="RENEWAL",
            event_id="evt-1",
        )

    assert outcome.downgraded and outcome.standard_missing
    assert row.status == "expired"
    messages = [call.args[0] for call in captured.call_args_list]
    assert "RevenueCat customer has entitlements but no 'standard'" in messages
    assert "RevenueCat sync removed access on an unexpected event" in messages
    downgrade = next(
        call
        for call in metric.call_args_list
        if call.args[0] == "subscription_access.downgrade"
    )
    assert downgrade.kwargs["attributes"]["event_type"] == "RENEWAL"


@pytest.mark.asyncio
async def test_expiration_downgrade_is_counted_but_not_reported_to_sentry():
    now = datetime.now(UTC)
    row = _granting_row(now)
    body = standard_annual_response(
        request_date=now, expires_date=now - timedelta(minutes=1)
    )

    with (
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        outcome = await sync_revenuecat_access(
            _SyncSession(_owner(), [row]),
            _owner(),
            snapshot=_snapshot(body, now),
            source="webhook",
            event_type="EXPIRATION",
        )

    assert outcome.downgraded
    captured.assert_not_called()
    assert "subscription_access.downgrade" in [
        call.args[0] for call in metric.call_args_list
    ]


@pytest.mark.asyncio
async def test_mark_unresolved_keeps_the_newest_marker_on_every_revenuecat_row():
    now = datetime.now(UTC)
    rows = [
        _granting_row(now, id="row-1", provider_sync_required_at=now),
        _granting_row(now, id="row-2"),
    ]

    marked = await mark_revenuecat_access_unresolved(
        _SyncSession(_owner(), rows), _owner(), since=now - timedelta(hours=1)
    )

    assert marked == 2
    assert [row.provider_sync_required_at for row in rows] == [
        now,
        now - timedelta(hours=1),
    ]
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access(rows, now)


@pytest.mark.asyncio
async def test_mark_unresolved_without_rows_adds_a_non_granting_placeholder():
    now = datetime.now(UTC)
    session = _SyncSession(_owner(), [])

    await mark_revenuecat_access_unresolved(
        session,
        _owner(),
        since=now,
        product_id="lifetime",
        platform="android",
        is_sandbox=True,
    )

    (placeholder,) = session.added
    assert (placeholder.product_id, placeholder.platform, placeholder.status) == (
        "lifetime",
        "android",
        "expired",
    )
    assert placeholder.provider == "revenuecat"
    assert placeholder.revenuecat_subscriber_id == "firebase-uid"
    assert placeholder.provider_sync_required_at == now
    # Flag on: unresolved (503, mobile falls back). Flag off: legacy no-access.
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([placeholder], now)
    assert placeholder.is_active() is False


def test_marked_row_keeps_the_legacy_check_while_the_flag_is_off():
    now = datetime.now(UTC)
    row = _granting_row(now, provider_sync_required_at=now)

    assert row.is_active() is True


def test_refund_while_billing_flag_is_set_normalizes_as_expired():
    now = datetime(2026, 10, 26, tzinfo=UTC)
    body = standard_annual_response(
        request_date=now,
        expires_date=now - timedelta(minutes=1),
        billing_issues_detected_at=now - timedelta(days=2),
        refunded_at=now - timedelta(minutes=1),
    )

    facts = normalize_standard_access_facts(body["subscriber"], now)

    assert facts is not None
    assert facts["status"] == "expired"


@pytest.mark.parametrize(
    ("reason", "capture"),
    [
        ("malformed", "capture_exception"),
        ("identity_mismatch", "capture_message"),
        # Outages and rate limits: the counter alerts, Sentry is not flooded.
        ("http_429", None),
        ("http_5xx", None),
        ("transport", None),
    ],
)
def test_snapshot_failures_are_logged_counted_and_reported(reason, capture, caplog):
    error = RevenueCatSnapshotUnavailable("RevenueCat failed", reason=reason)

    with (
        patch(f"{SYNC}.capture_exception") as exception,
        patch(f"{SYNC}.capture_message") as message,
        patch(f"{SYNC}.increment_metric") as metric,
        caplog.at_level("WARNING"),
    ):
        record_snapshot_unavailable(
            error,
            source="webhook",
            user_id="user-1",
            event_type="RENEWAL",
            event_id="evt-1",
        )

    reported = {"capture_exception": exception, "capture_message": message}
    for name, capture_mock in reported.items():
        if name != capture:
            capture_mock.assert_not_called()
    if capture is not None:
        reported[capture].assert_called_once()
        context = reported[capture].call_args.kwargs["context"]
        assert context["user_id"] == "user-1"
        assert context["event_id"] == "evt-1"
        assert context["failure_kind"] == reason
    metric.assert_called_once_with(
        "subscription_access.snapshot_unavailable",
        attributes={
            "source": "webhook",
            "failure_kind": reason,
            "event_type": "RENEWAL",
        },
    )
    assert "user_id=user-1" in caplog.text and "event_id=evt-1" in caplog.text


@pytest.mark.asyncio
async def test_sync_applies_a_real_adapter_read_without_patching_either():
    """Adapter and sync together: only the HTTP transport is stubbed."""
    now = datetime.now(UTC).replace(microsecond=0)
    body = standard_annual_response(
        request_date=now,
        expires_date=now + timedelta(days=365),
        store="play_store",
        store_transaction_id="GPA.1234-5678",
    )
    session = _SyncSession(_owner(), [])

    with patched_revenuecat_http(body) as get:
        outcome = await sync_revenuecat_access(
            session, _owner(), adapter=RevenueCatAdapter(api_key="test-key")
        )

    assert get.await_args.args[0].endswith("/subscribers/firebase-uid")
    assert outcome.result == "applied"
    (row,) = session.added
    assert row.provider_snapshot_at == now
    assert (row.status, row.platform, row.product_id) == ("active", "android", "annual")
    assert row.store_transaction_id == "GPA.1234-5678"
    assert row.expires_at == now + timedelta(days=365)
    assert evaluate_subscription_access([row], now).has_access is True

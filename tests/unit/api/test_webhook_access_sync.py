"""RevenueCat webhook -> access sync, end to end through the real code.

Only two things are stubbed: the HTTP transport under the real RevenueCat
adapter, and the database session (an in-memory double that, like the real
AsyncSession here, does not autoflush). The webhook route, the legacy event
handlers, the referral wallet arithmetic, the adapter parser, the access
sync and the access policy all run for real.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.sql.elements import TextClause
from tests.fixtures.revenuecat_samples import (
    empty_customer_response,
    standard_annual_response,
)

from src.api.routes.v1 import webhooks
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.referral.referral_conversion import ReferralConversion
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.services.subscription_access_sync_service import (
    sync_revenuecat_access,
)

SYNC = "src.infra.services.subscription_access_sync_service"


class _Referrals:
    """Referral repo double with the real wallet arithmetic."""

    def __init__(self, conversion: ReferralConversion | None = None):
        self.conversion = conversion
        self.balance = 0

    async def get_conversion_by_referred_user(self, _user_id, for_update=False):
        return self.conversion

    async def credit_wallet(self, _user_id, amount):
        self.balance += amount

    async def revoke_from_wallet(self, _user_id, amount):
        self.balance = max(0, self.balance - amount)


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return self._rows


class _Database:
    """Async session double. Like the app's session it never autoflushes:
    a SELECT sees only rows that were flushed."""

    def __init__(self, users: list[User], rows: list[Subscription] | None = None):
        self.users = {user.id: user for user in users}
        self.rows: list[Subscription] = list(rows or [])
        self.pending: list[Subscription] = []
        self.purges: list[Any] = []

    def _params(self, statement) -> dict[str, Any]:
        return statement.compile().params

    def _users_for(self, statement) -> list[User]:
        params = self._params(statement)
        if "firebase_uid_1" in params:
            return [
                user
                for user in self.users.values()
                if user.firebase_uid == params["firebase_uid_1"]
            ]
        return [user for user in self.users.values() if user.id == params["id_1"]]

    async def execute(self, statement, params=None):
        if isinstance(statement, TextClause):  # trial-push purge
            self.purges.append(params)
            return _Result([])
        return _Result(self._users_for(statement))

    async def scalar(self, statement):
        return _Result(self._users_for(statement)).first()

    async def scalars(self, statement):
        params = self._params(statement)
        return _Result(
            [
                row
                for row in self.rows
                if row.user_id == params["user_id_1"]
                and row.provider == params["provider_1"]
            ]
        )

    def add(self, row):
        if isinstance(row, Subscription):
            self.pending.append(row)

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def flush(self):
        for row in self.pending:
            row.provider = row.provider or "revenuecat"
            row.created_at = row.created_at or datetime.now(UTC)
        self.rows.extend(self.pending)
        self.pending = []

    def rows_of(self, user: User) -> list[Subscription]:
        return [row for row in self.rows + self.pending if row.user_id == user.id]


class _Subscriptions:
    def __init__(self, database: _Database):
        self.database = database

    async def find_by_revenuecat_id(self, revenuecat_id, user_id=None):
        matches = [
            row
            for row in self.database.rows
            if row.revenuecat_subscriber_id == revenuecat_id
            and row.provider == "revenuecat"
            and (user_id is None or row.user_id == user_id)
        ]
        return max(matches, key=lambda row: row.purchased_at, default=None)


class _UnitOfWork:
    def __init__(self, database: _Database, referrals: _Referrals):
        self.session = database
        self.referrals = referrals
        self.subscriptions = _Subscriptions(database)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


@contextmanager
def _revenuecat(answers: dict[str, Callable[[], tuple[int, Any]]]) -> Iterator[Any]:
    """Stub the adapter's HTTP transport per app user id."""

    async def get(url, **_kwargs):
        app_user_id = url.rsplit("/", 1)[-1]
        status_code, body = answers[app_user_id]()
        response = MagicMock(status_code=status_code, headers={})
        response.json.return_value = body
        return response

    with patch("httpx.AsyncClient") as client:
        recorded = AsyncMock(side_effect=get)
        client.return_value.__aenter__.return_value.get = recorded
        yield recorded


def _user(user_id: str, firebase_uid: str) -> User:
    return User(id=user_id, firebase_uid=firebase_uid)


def _row(user: User, **values) -> Subscription:
    now = datetime.now(UTC)
    return Subscription(
        **{
            "id": f"{user.id}-row",
            "user_id": user.id,
            "revenuecat_subscriber_id": user.firebase_uid,
            "provider": "revenuecat",
            "product_id": "annual",
            "platform": "ios",
            "status": "active",
            "purchased_at": now - timedelta(days=300),
            "expires_at": now + timedelta(days=65),
            "period_type": "normal",
            "provider_snapshot_at": now - timedelta(days=1),
            "is_sandbox": False,
            "created_at": now - timedelta(days=300),
        }
        | values
    )


def _ok(body: dict) -> Callable[[], tuple[int, Any]]:
    return lambda: (200, body)


def _fails(status_code: int = 503) -> Callable[[], tuple[int, Any]]:
    return lambda: (status_code, None)


async def _deliver(
    monkeypatch,
    event: dict,
    database: _Database,
    referrals: _Referrals | None = None,
    publisher: Any = None,
) -> dict:
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.delenv("REVENUECAT_WEBHOOK_AUTH_HEADER", raising=False)
    monkeypatch.delenv("AFFILIATE_INTEGRATION_ENABLED", raising=False)
    uow = _UnitOfWork(database, referrals or _Referrals())
    request = MagicMock()
    request.json = AsyncMock(return_value={"event": event})
    with (
        patch.object(webhooks, "AsyncUnitOfWork", side_effect=lambda: uow),
        patch.object(
            webhooks,
            "_get_subscription_service",
            return_value=RevenueCatAdapter(api_key="test-key"),
        ),
        patch.object(
            webhooks,
            "_get_event_publisher",
            return_value=publisher or MagicMock(publish=AsyncMock()),
        ),
    ):
        return await webhooks.revenuecat_webhook(
            request, authorization="webhook-secret"
        )


def _event(event_type: str, app_user_id: str, **values) -> dict:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "id": f"evt-{event_type.lower()}",
        "type": event_type,
        "app_user_id": app_user_id,
        "product_id": "annual",
        "store": "APP_STORE",
        "environment": "PRODUCTION",
        "event_timestamp_ms": now_ms,
        "purchased_at_ms": now_ms,
        "expiration_at_ms": now_ms + 30 * 86_400_000,
        "transaction_id": "1000000123456789",
    } | values


def _annual(expires_in: timedelta = timedelta(days=365)) -> dict:
    now = datetime.now(UTC).replace(microsecond=0)
    return standard_annual_response(request_date=now, expires_date=now + expires_in)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["true", "false"])
async def test_snapshot_failure_keeps_legacy_effects_and_leaves_access_unresolved(
    monkeypatch, flag
):
    """RevenueCat down: the purchase, referral credit and lifecycle event still
    happen, the webhook ACKs, and access is left unresolved for repair."""
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", flag)
    user = _user("user-u", "uid-u")
    database = _Database([user])
    referrals = _Referrals(
        ReferralConversion(
            referrer_user_id="referrer-1",
            referred_user_id="user-u",
            code_used="ABC123",
            status="pending",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
    )
    publisher = MagicMock(publish=AsyncMock())
    event = _event("INITIAL_PURCHASE", "uid-u")

    with (
        _revenuecat({"uid-u": _fails(503)}) as revenuecat,
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        result = await _deliver(monkeypatch, event, database, referrals, publisher)

    assert result == {"status": "success"}
    revenuecat.assert_awaited_once()
    assert referrals.conversion.status == "converted"
    assert referrals.balance == 50000
    lifecycle = publisher.publish.await_args.args[0]["data"]["lifecycle_type"]
    assert lifecycle == "subscription_initial_purchase"
    (row,) = database.rows_of(user)
    assert row.status == "active"
    assert row.provider_sync_required_at == datetime.fromtimestamp(
        event["event_timestamp_ms"] / 1000, UTC
    )
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access(database.rows_of(user))
    context = captured.call_args.kwargs["context"]
    assert (context["user_id"], context["event_id"], context["failure_kind"]) == (
        "user-u",
        "evt-initial_purchase",
        "http_5xx",
    )
    assert any(
        call.args[0] == "subscription_access.snapshot_unavailable"
        for call in metric.call_args_list
    )


@pytest.mark.asyncio
async def test_initial_purchase_leaves_exactly_one_reconciled_row(monkeypatch):
    user = _user("user-u", "uid-u")
    database = _Database([user])
    body = _annual()

    with _revenuecat({"uid-u": _ok(body)}):
        await _deliver(monkeypatch, _event("INITIAL_PURCHASE", "uid-u"), database)

    (row,) = database.rows_of(user)
    assert row.provider_snapshot_at == datetime.fromtimestamp(
        body["request_date_ms"] / 1000, UTC
    )
    assert row.provider_sync_required_at is None
    assert evaluate_subscription_access([row]).has_access is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        "SUBSCRIPTION_EXTENDED",
        "TEMPORARY_ENTITLEMENT_GRANT",
        "REFUND_REVERSED",
        "UNCANCELLATION",
        "NON_RENEWING_PURCHASE",
        "SUBSCRIPTION_PAUSED",
        "PRODUCT_CHANGE",
        "A_TYPE_REVENUECAT_ADDS_LATER",
    ],
)
async def test_every_access_event_type_is_synced_from_the_snapshot(
    monkeypatch, event_type
):
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])
    body = _annual(expires_in=timedelta(days=400))

    with _revenuecat({"uid-u": _ok(body)}) as revenuecat:
        result = await _deliver(monkeypatch, _event(event_type, "uid-u"), database)

    assert result == {"status": "success"}
    revenuecat.assert_awaited_once()
    assert stored.expires_at == datetime.fromisoformat(
        body["subscriber"]["entitlements"]["standard"]["expires_date"].replace(
            "Z", "+00:00"
        )
    )
    assert stored.provider_snapshot_at == datetime.fromtimestamp(
        body["request_date_ms"] / 1000, UTC
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["TEST", "EXPERIMENT_ENROLLMENT"])
async def test_access_neutral_events_never_read_revenuecat(monkeypatch, event_type):
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])

    with _revenuecat({}) as revenuecat:
        result = await _deliver(monkeypatch, _event(event_type, "uid-u"), database)

    assert result == {"status": "success"}
    revenuecat.assert_not_awaited()
    assert stored.provider_sync_required_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refund_fields",
    [
        {"cancel_reason": "CUSTOMER_SUPPORT"},
        {"cancel_reason": "UNKNOWN", "price": -2.99},
        {"cancel_reason": "UNKNOWN", "price_in_purchased_currency": -79000},
    ],
)
async def test_refund_clawback_runs_while_the_snapshot_still_grants(
    monkeypatch, refund_fields
):
    """Refunds can leave the subscription active; the clawback must not wait
    for the snapshot to show the entitlement expired."""
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])
    referrals = _Referrals(
        ReferralConversion(
            referrer_user_id="referrer-1",
            referred_user_id="user-u",
            code_used="ABC123",
            status="converted",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
    )
    referrals.balance = 80000
    publisher = MagicMock(publish=AsyncMock())

    with _revenuecat({"uid-u": _ok(_annual())}):
        await _deliver(
            monkeypatch,
            _event("CANCELLATION", "uid-u", **refund_fields),
            database,
            referrals,
            publisher,
        )

    assert referrals.conversion.status == "revoked"
    assert referrals.balance == 30000
    lifecycle = publisher.publish.await_args.args[0]["data"]["lifecycle_type"]
    assert lifecycle == "subscription_refund"
    # Access follows the snapshot, which still grants.
    assert stored.status == "active"
    assert evaluate_subscription_access([stored]).has_access is True


@pytest.mark.asyncio
async def test_plain_unsubscribe_is_not_a_refund(monkeypatch):
    user = _user("user-u", "uid-u")
    database = _Database([user], [_row(user)])
    referrals = _Referrals(
        ReferralConversion(
            referrer_user_id="referrer-1",
            referred_user_id="user-u",
            code_used="ABC123",
            status="converted",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
    )

    with _revenuecat({"uid-u": _ok(_annual())}):
        await _deliver(
            monkeypatch,
            _event("CANCELLATION", "uid-u", cancel_reason="UNSUBSCRIBE", price=2.99),
            database,
            referrals,
        )

    assert referrals.conversion.status == "converted"


@pytest.mark.asyncio
async def test_event_for_another_identity_is_not_read_under_the_firebase_uid(
    monkeypatch,
):
    """Reading the uid would make RevenueCat create an empty customer and would
    describe a different customer than the one the event is about."""
    user = _user("user-u", "uid-u")
    legacy = _row(user, revenuecat_subscriber_id="$RCAnonymousID:legacy")
    database = _Database([user], [legacy])

    with (
        _revenuecat({}) as revenuecat,
        patch(f"{SYNC}.capture_message") as captured,
    ):
        result = await _deliver(
            monkeypatch,
            _event("RENEWAL", "$RCAnonymousID:legacy"),
            database,
        )

    assert result == {"status": "success"}
    revenuecat.assert_not_awaited()
    assert legacy.provider_sync_required_at is not None
    assert captured.call_args.kwargs["context"]["failure_kind"] == "identity_mismatch"


def _transfer(source: str, target: str) -> dict:
    return {
        "id": "evt-transfer",
        "type": "TRANSFER",
        "transferred_from": [source],
        "transferred_to": [target],
        "store": "APP_STORE",
        "environment": "PRODUCTION",
        "event_timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
    }


def _web_only_customer() -> dict:
    body = _annual()
    subscriber = body["subscriber"]
    web = subscriber["subscriptions"].pop("annual") | {
        "store": "stripe",
        "store_transaction_id": "sub_web_1",
    }
    subscriber["subscriptions"] = {"web_monthly": web}
    subscriber["entitlements"]["standard"]["product_identifier"] = "web_monthly"
    return body


@pytest.mark.asyncio
async def test_transfer_moves_only_what_each_parties_snapshot_supports(monkeypatch):
    """A restore moves A's App Store purchase to B; A keeps its web plan."""
    source = _user("user-a", "uid-a")
    target = _user("user-b", "uid-b")
    store_row = _row(source, id="a-annual")
    web_row = _row(source, id="a-web", product_id="web_monthly", platform="web")
    momo_row = _row(
        source, id="a-momo", product_id="momo_pass", provider="momo", platform="web"
    )
    momo_snapshot_at = momo_row.provider_snapshot_at
    database = _Database([source, target], [store_row, web_row, momo_row])

    with _revenuecat({"uid-a": _ok(_web_only_customer()), "uid-b": _ok(_annual())}):
        result = await _deliver(monkeypatch, _transfer("uid-a", "uid-b"), database)

    assert result == {"status": "success"}
    assert store_row.status == "expired"
    assert store_row.revenuecat_subscriber_id == "uid-a"  # never re-pointed
    assert web_row.status == "active"
    assert (momo_row.status, momo_row.provider_snapshot_at) == (
        "active",
        momo_snapshot_at,
    )
    assert evaluate_subscription_access(database.rows_of(source)).has_access is True
    (target_row,) = database.rows_of(target)
    assert (target_row.product_id, target_row.status) == ("annual", "active")
    assert evaluate_subscription_access([target_row]).has_access is True


@pytest.mark.asyncio
async def test_transfer_source_emptied_by_revenuecat_loses_access(monkeypatch):
    source = _user("user-a", "uid-a")
    target = _user("user-b", "uid-b")
    store_row = _row(source)
    database = _Database([source, target], [store_row])
    empty = empty_customer_response(app_user_id="uid-a")

    with _revenuecat({"uid-a": _ok(empty), "uid-b": _ok(_annual())}):
        await _deliver(monkeypatch, _transfer("uid-a", "uid-b"), database)

    assert store_row.status == "expired"
    assert store_row.provider_sync_required_at is None
    assert evaluate_subscription_access([store_row]).has_access is False


@pytest.mark.asyncio
async def test_transfer_never_expires_a_source_it_could_not_read(monkeypatch):
    source = _user("user-a", "uid-a")
    target = _user("user-b", "uid-b")
    store_row = _row(source)
    database = _Database([source, target], [store_row])

    with _revenuecat({"uid-a": _fails(429), "uid-b": _ok(_annual())}):
        result = await _deliver(monkeypatch, _transfer("uid-a", "uid-b"), database)

    assert result == {"status": "success"}
    assert store_row.status == "active"
    assert store_row.provider_sync_required_at is not None
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([store_row])
    (target_row,) = database.rows_of(target)
    assert evaluate_subscription_access([target_row]).has_access is True


@pytest.mark.asyncio
async def test_a_late_event_with_a_stale_snapshot_keeps_the_verified_facts(
    monkeypatch,
):
    """A late RENEWAL's legacy write must not overwrite newer verified facts."""
    user = _user("user-u", "uid-u")
    verified_at = datetime.now(UTC).replace(microsecond=0)
    verified_expiry = verified_at + timedelta(days=365)
    stored = _row(user, expires_at=verified_expiry, provider_snapshot_at=verified_at)
    database = _Database([user], [stored])
    stale = standard_annual_response(
        request_date=verified_at - timedelta(minutes=5),
        expires_date=verified_at + timedelta(days=30),
    )

    with _revenuecat({"uid-u": _ok(stale)}):
        result = await _deliver(monkeypatch, _event("RENEWAL", "uid-u"), database)

    assert result == {"status": "success"}
    assert (stored.expires_at, stored.provider_snapshot_at) == (
        verified_expiry,
        verified_at,
    )
    assert stored.provider_sync_required_at is None


def _metrics(metric: MagicMock, name: str) -> list[Any]:
    return [call for call in metric.call_args_list if call.args[0] == name]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "fields", "revoked"),
    [
        ("EXPIRATION", {}, "expired"),
        ("CANCELLATION", {"cancel_reason": "CUSTOMER_SUPPORT"}, "refunded"),
    ],
)
async def test_expected_downgrade_is_measured_from_the_access_before_the_event(
    monkeypatch, event_type, fields, revoked
):
    """The legacy handler already wrote 'expired'/'refunded' when the sync
    runs; the downgrade must still be counted against the verified grant."""
    user = _user("user-u", "uid-u")
    stored = _row(user)  # verified, paid for 65 more days
    database = _Database([user], [stored])

    with (
        _revenuecat({"uid-u": _ok(_annual(expires_in=timedelta(days=-1)))}),
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        result = await _deliver(
            monkeypatch, _event(event_type, "uid-u", **fields), database
        )

    assert result == {"status": "success"}
    assert stored.status == revoked
    (downgrade,) = _metrics(metric, "subscription_access.downgrade")
    assert downgrade.kwargs["attributes"] == {
        "source": "webhook",
        "event_type": event_type,
        "result": "applied",
        "expected": "true",
    }
    captured.assert_not_called()


@pytest.mark.asyncio
async def test_late_renewal_for_a_user_without_access_is_not_a_downgrade(
    monkeypatch,
):
    """handle_renewal sets the row active before the sync; the user never had
    access, so RevenueCat confirming the expiry removes nothing."""
    user = _user("user-u", "uid-u")
    lapsed_at = datetime.now(UTC) - timedelta(days=2)
    stored = _row(user, status="expired", expires_at=lapsed_at)
    database = _Database([user], [stored])

    with (
        _revenuecat({"uid-u": _ok(_annual(expires_in=timedelta(days=-2)))}),
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        await _deliver(monkeypatch, _event("RENEWAL", "uid-u"), database)

    assert (stored.status, stored.expires_at) == ("expired", lapsed_at)
    assert evaluate_subscription_access([stored]).has_access is False
    assert _metrics(metric, "subscription_access.downgrade") == []
    captured.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_downgrade_through_the_handlers_is_alerted(monkeypatch):
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])

    with (
        _revenuecat({"uid-u": _ok(_annual(expires_in=timedelta(days=-1)))}),
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        await _deliver(monkeypatch, _event("RENEWAL", "uid-u"), database)

    (downgrade,) = _metrics(metric, "subscription_access.downgrade")
    assert downgrade.kwargs["attributes"]["expected"] == "false"
    assert [call.args[0] for call in captured.call_args_list] == [
        "RevenueCat sync removed access on an unexpected event"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "fields"),
    [("EXPIRATION", {}), ("CANCELLATION", {"cancel_reason": "CUSTOMER_SUPPORT"})],
)
async def test_empty_customer_never_resolves_a_verified_grant_to_no_access(
    monkeypatch, event_type, fields
):
    """The absence guard judges the verified grant, not the legacy handler's
    'expired'/'refunded' write, so the user is left unresolved for repair.
    The event's own write stands, as when RevenueCat cannot be read, so the
    flag-off legacy check answers as it did before this change."""
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])

    with (
        _revenuecat({"uid-u": _ok(empty_customer_response(app_user_id="uid-u"))}),
        patch(f"{SYNC}.capture_message") as captured,
    ):
        result = await _deliver(
            monkeypatch, _event(event_type, "uid-u", **fields), database
        )

    assert result == {"status": "success"}
    assert stored.provider_sync_required_at is not None
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([stored])
    assert captured.call_args.kwargs["context"]["failure_kind"] == (
        "absent_with_history"
    )
    assert stored.is_active() is False  # flag off: the event's write, as before


def _pending_referral() -> _Referrals:
    return _Referrals(
        ReferralConversion(
            referrer_user_id="referrer-1",
            referred_user_id="user-u",
            code_used="ABC123",
            status="pending",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["true", "false"])
async def test_initial_purchase_after_a_refresh_still_credits_the_referral(
    monkeypatch, flag
):
    """The app refreshes right after buying, so the row often exists before
    the INITIAL_PURCHASE webhook lands. It is still the first purchase."""
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", flag)
    user = _user("user-u", "uid-u")
    database = _Database([user])
    referrals = _pending_referral()
    publisher = MagicMock(publish=AsyncMock())
    body = _annual()

    # POST /v1/me/entitlements/refresh applies the snapshot first, exactly as
    # RevenueCatAccessRefreshService does.
    with _revenuecat({"uid-u": _ok(body)}):
        snapshot = await RevenueCatAdapter(api_key="k").get_verified_snapshot("uid-u")
    await sync_revenuecat_access(database, user, snapshot=snapshot, source="refresh")
    await database.flush()

    with _revenuecat({"uid-u": _ok(body)}):
        result = await _deliver(
            monkeypatch,
            _event("INITIAL_PURCHASE", "uid-u"),
            database,
            referrals,
            publisher,
        )

    assert result == {"status": "success"}
    assert (referrals.conversion.status, referrals.balance) == ("converted", 50000)
    (published,) = publisher.publish.await_args_list
    assert published.args[0]["data"]["lifecycle_type"] == (
        "subscription_initial_purchase"
    )
    assert database.purges == []  # a purchase is not a renewal
    (row,) = database.rows_of(user)
    assert evaluate_subscription_access([row]).has_access is True


@pytest.mark.asyncio
async def test_initial_purchase_over_an_unresolved_placeholder_credits_and_resolves(
    monkeypatch,
):
    """An earlier event whose read failed left a placeholder row."""
    user = _user("user-u", "uid-u")
    database = _Database([user])
    referrals = _pending_referral()
    publisher = MagicMock(publish=AsyncMock())
    earlier = _event("TEMPORARY_ENTITLEMENT_GRANT", "uid-u")

    with (
        _revenuecat({"uid-u": _fails(503)}),
        patch(f"{SYNC}.capture_message"),
    ):
        await _deliver(monkeypatch, earlier, database)
    await database.flush()
    (placeholder,) = database.rows_of(user)
    assert placeholder.provider_sync_required_at is not None

    read_at = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
    body = standard_annual_response(
        request_date=read_at, expires_date=read_at + timedelta(days=365)
    )
    with _revenuecat({"uid-u": _ok(body)}):
        await _deliver(
            monkeypatch,
            _event("INITIAL_PURCHASE", "uid-u"),
            database,
            referrals,
            publisher,
        )

    assert (referrals.conversion.status, referrals.balance) == ("converted", 50000)
    (published,) = publisher.publish.await_args_list
    assert published.args[0]["data"]["lifecycle_type"] == (
        "subscription_initial_purchase"
    )
    (row,) = database.rows_of(user)
    assert row.provider_sync_required_at is None
    assert evaluate_subscription_access([row]).has_access is True

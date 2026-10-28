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
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.sql.elements import TextClause
from tests.fixtures.revenuecat_samples import (
    empty_customer_response,
    standard_annual_response,
)

from src.api.routes.v1 import webhooks
from src.app.handlers.query_handlers.check_subscription_access_query_handler import (
    CheckSubscriptionAccessQueryHandler,
)
from src.app.queries.subscription_access import CheckSubscriptionAccessQuery
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable
from src.infra.adapters.revenuecat_adapter import RevenueCatAdapter
from src.infra.database.models.referral.referral_conversion import ReferralConversion
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.repositories.subscription_repository_async import product_match_rank
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

    async def find_by_revenuecat_id(self, revenuecat_id, user_id=None, product_id=None):
        """The repository's order: the event's product first (the repository's
        own product_match_rank), then newest."""
        matches = [
            row
            for row in self.database.rows
            if row.revenuecat_subscriber_id == revenuecat_id
            and row.provider == "revenuecat"
            and (user_id is None or row.user_id == user_id)
        ]
        return min(
            matches,
            key=lambda row: (
                product_match_rank(row.product_id, product_id) if product_id else 0,
                -row.purchased_at.timestamp(),
            ),
            default=None,
        )

    async def find_by_user_id(self, user_id):
        return self.database.rows_of(self.database.users[user_id])


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


def _raises(error: Exception) -> Callable[[], tuple[int, Any]]:
    """A transport failure the adapter does not translate (not an httpx error)."""

    def answer() -> tuple[int, Any]:
        raise error

    return answer


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
    monkeypatch, flag, caplog
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
        caplog.at_level("WARNING", logger=SYNC),
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
    # An outage is logged per event and alerted on the counter; it is not
    # sent to Sentry once per webhook.
    assert "user_id=user-u" in caplog.text
    assert "event_id=evt-initial_purchase" in caplog.text
    assert "reason=http_5xx" in caplog.text
    (unavailable,) = _metrics(metric, "subscription_access.snapshot_unavailable")
    assert unavailable.kwargs["attributes"]["failure_kind"] == "http_5xx"
    captured.assert_not_called()


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
@pytest.mark.parametrize(("answer", "forgotten"), [(_ok, True), (_fails, False)])
async def test_a_synced_event_lets_a_refused_paid_route_ask_revenuecat_again(
    monkeypatch, answer, forgotten
):
    """After the commit of a synced event, the paid-route RevenueCat check is
    not held to an answer older than this read; a failed read changes nothing."""
    user = _user("user-u", "uid-u")
    database = _Database([user])
    forget = AsyncMock()
    monkeypatch.setattr(webhooks, "forget_revenuecat_check", forget)
    reply = answer(_annual()) if answer is _ok else answer(503)

    with _revenuecat({"uid-u": reply}):
        await _deliver(monkeypatch, _event("INITIAL_PURCHASE", "uid-u"), database)

    if forgotten:
        forget.assert_awaited_once_with("user-u")
    else:
        forget.assert_not_called()


@pytest.mark.asyncio
async def test_a_synced_transfer_lets_each_synced_party_ask_revenuecat_again(
    monkeypatch,
):
    source = _user("user-a", "uid-a")
    target = _user("user-b", "uid-b")
    database = _Database([source, target], [_row(source)])
    forget = AsyncMock()
    monkeypatch.setattr(webhooks, "forget_revenuecat_check", forget)

    with _revenuecat({"uid-a": _fails(503), "uid-b": _ok(_annual())}):
        await _deliver(monkeypatch, _transfer("uid-a", "uid-b"), database)

    forget.assert_awaited_once_with("user-b")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["EXPIRATION", "CANCELLATION", "BILLING_ISSUE"])
async def test_an_old_products_event_never_lands_on_the_newer_products_row(
    monkeypatch, event_type
):
    """Upgrade monthly -> annual, then the monthly product's event arrives
    while RevenueCat is down. Nothing can undo a legacy write on the annual
    row then: it must go to the monthly row, so the legacy fallback still
    grants the paying user while the markers wait for repair."""
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")
    user = _user("user-u", "uid-u")
    now = datetime.now(UTC)
    monthly = _row(
        user,
        id="u-monthly",
        product_id="onemonth",
        status="expired",
        purchased_at=now - timedelta(days=400),
        expires_at=now - timedelta(days=2),
        created_at=now - timedelta(days=400),
    )
    annual = _row(
        user,
        id="u-annual",
        product_id="annual",
        purchased_at=now - timedelta(days=2),
        expires_at=now + timedelta(days=363),
        created_at=now - timedelta(days=2),
    )
    database = _Database([user], [monthly, annual])

    with _revenuecat({"uid-u": _fails(503)}):
        result = await _deliver(
            monkeypatch, _event(event_type, "uid-u", product_id="onemonth"), database
        )

    assert result == {"status": "success"}
    assert (annual.status, annual.product_id) == ("active", "annual")
    assert annual.expires_at == now + timedelta(days=363)
    assert monthly.status != "active"
    assert monthly.provider_sync_required_at is not None
    assert annual.provider_sync_required_at is not None
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access(database.rows_of(user))
    enforcement = CheckSubscriptionAccessQueryHandler(
        uow_factory=lambda: _UnitOfWork(database, _Referrals())
    )
    assert await enforcement.handle(CheckSubscriptionAccessQuery(user.id)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["EXPIRATION", "CANCELLATION", "BILLING_ISSUE"])
@pytest.mark.parametrize(
    ("stored_monthly", "event_monthly"),
    [("onemonth", "onemonth:p1m"), ("onemonth:p1m", "onemonth")],
    ids=["snapshot-row", "legacy-row"],
)
async def test_an_old_play_products_event_never_lands_on_the_newer_products_row(
    monkeypatch, event_type, stored_monthly, event_monthly
):
    """The Android variant: Play webhooks name '<subscription>:<base plan>',
    snapshot rows the bare subscription id. The old product's event must
    still find the old product's row, either way round."""
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")
    user = _user("user-u", "uid-u")
    now = datetime.now(UTC)
    monthly = _row(
        user,
        id="u-monthly",
        product_id=stored_monthly,
        platform="android",
        status="expired",
        purchased_at=now - timedelta(days=400),
        expires_at=now - timedelta(days=2),
        created_at=now - timedelta(days=400),
    )
    annual = _row(
        user,
        id="u-annual",
        product_id="annual",
        platform="android",
        purchased_at=now - timedelta(days=2),
        expires_at=now + timedelta(days=363),
        created_at=now - timedelta(days=2),
    )
    database = _Database([user], [monthly, annual])

    with _revenuecat({"uid-u": _fails(503)}):
        result = await _deliver(
            monkeypatch,
            _event(event_type, "uid-u", product_id=event_monthly, store="PLAY_STORE"),
            database,
        )

    assert result == {"status": "success"}
    assert (annual.status, annual.expires_at) == (
        "active",
        now + timedelta(days=363),
    )
    assert monthly.status != "active"
    enforcement = CheckSubscriptionAccessQueryHandler(
        uow_factory=lambda: _UnitOfWork(database, _Referrals())
    )
    assert await enforcement.handle(CheckSubscriptionAccessQuery(user.id)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "written"),
    [
        (
            {"type": "CANCELLATION", "cancel_reason": "UNSUBSCRIBE"},
            "cancelled",
        ),
        ({"type": "BILLING_ISSUE"}, "billing_issue"),
    ],
)
async def test_enforcement_keeps_a_paying_user_whose_webhook_read_failed(
    monkeypatch, event, written
):
    """Auto-renew off (or a failed charge), and RevenueCat answers 503: the
    legacy handler writes the row and every row is marked unresolved. The
    paid routes keep answering for the paid-through user."""
    user = _user("user-u", "uid-u")
    grace_end = datetime.now(UTC) + timedelta(days=5)
    stored = _row(user, grace_period_expires_at=grace_end)
    database = _Database([user], [stored])

    with _revenuecat({"uid-u": _fails(503)}):
        result = await _deliver(
            monkeypatch, _event(event.pop("type"), "uid-u", **event), database
        )

    assert result == {"status": "success"}
    assert stored.status == written
    assert stored.provider_sync_required_at is not None
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access(database.rows_of(user))
    enforcement = CheckSubscriptionAccessQueryHandler(
        uow_factory=lambda: _UnitOfWork(database, _Referrals())
    )
    assert await enforcement.handle(CheckSubscriptionAccessQuery(user.id)) is True


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


REFUND_FIELDS = [
    {"cancel_reason": "CUSTOMER_SUPPORT"},
    {"cancel_reason": "UNKNOWN", "price": -2.99},
    {"cancel_reason": "UNKNOWN", "price_in_purchased_currency": -79000},
]


def _referral(status: str) -> _Referrals:
    referrals = _Referrals(
        ReferralConversion(
            referrer_user_id="referrer-1",
            referred_user_id="user-u",
            code_used="ABC123",
            status=status,
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
    )
    referrals.balance = 80000
    return referrals


@pytest.mark.asyncio
@pytest.mark.parametrize("conversion_status", ["converted", "pending"])
@pytest.mark.parametrize("refund_fields", REFUND_FIELDS)
async def test_refund_cancellation_moves_no_referral_money(
    monkeypatch, refund_fields, conversion_status
):
    """Production (20e0d5aa) sends a refund CANCELLATION through
    handle_cancellation: no commission is clawed back or credited, and the
    one lifecycle event is 'subscription_canceled', as for any cancellation.
    The refund clawback stays off pending a product decision."""
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])
    referrals = _referral(conversion_status)
    publisher = MagicMock(publish=AsyncMock())
    event = _event("CANCELLATION", "uid-u", **refund_fields)

    with _revenuecat({"uid-u": _ok(_annual())}):
        result = await _deliver(monkeypatch, event, database, referrals, publisher)

    assert result == {"status": "success"}
    assert (referrals.conversion.status, referrals.balance) == (
        conversion_status,
        80000,
    )
    publisher.publish.assert_awaited_once()
    data = publisher.publish.await_args.args[0]["data"]
    assert data["lifecycle_type"] == "subscription_canceled"
    assert {key: data[key] for key in data if key != "occurred_at"} == {
        "lifecycle_type": "subscription_canceled",
        "mealtrack_user_id": "user-u",
        "product_id": "annual",
        "subscription_id": event["transaction_id"],
    }
    # Access follows the snapshot, which still grants.
    assert stored.status == "active"
    assert evaluate_subscription_access([stored]).has_access is True


@pytest.mark.asyncio
async def test_a_refund_that_ends_access_revokes_it_from_the_snapshot(monkeypatch):
    """The refunded user still loses access: the snapshot no longer grants, so
    the sync revokes the row as 'refunded', an expected downgrade."""
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])
    referrals = _referral("converted")
    publisher = MagicMock(publish=AsyncMock())
    refunded = _annual(expires_in=timedelta(minutes=-5))

    with (
        _revenuecat({"uid-u": _ok(refunded)}),
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        await _deliver(
            monkeypatch,
            _event("CANCELLATION", "uid-u", cancel_reason="CUSTOMER_SUPPORT"),
            database,
            referrals,
            publisher,
        )

    assert stored.status == "refunded"
    assert stored.provider_sync_required_at is None
    assert evaluate_subscription_access([stored]).has_access is False
    (downgrade,) = _metrics(metric, "subscription_access.downgrade")
    assert downgrade.kwargs["attributes"]["expected"] == "true"
    captured.assert_not_called()
    assert (referrals.conversion.status, referrals.balance) == ("converted", 80000)
    data = publisher.publish.await_args.args[0]["data"]
    assert data["lifecycle_type"] == "subscription_canceled"


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

    with (
        _revenuecat({"uid-a": _ok(empty), "uid-b": _ok(_annual())}),
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        await _deliver(monkeypatch, _transfer("uid-a", "uid-b"), database)

    assert store_row.status == "expired"
    assert store_row.provider_sync_required_at is None
    assert evaluate_subscription_access([store_row]).has_access is False
    # Losing what RevenueCat moved away is the transfer itself: counted as
    # expected, never paged on or sent to Sentry as an unexpected downgrade.
    (downgrade,) = _metrics(metric, "subscription_access.downgrade")
    assert downgrade.kwargs["attributes"] == {
        "source": "webhook",
        "event_type": "TRANSFER",
        "result": "applied",
        "expected": "true",
    }
    captured.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_target_losing_access_is_still_unexpected(monkeypatch):
    source = _user("user-a", "uid-a")
    target = _user("user-b", "uid-b")
    target_row = _row(target)
    database = _Database([source, target], [target_row])
    lapsed = _annual(expires_in=timedelta(days=-1))

    with (
        _revenuecat({"uid-a": _ok(_web_only_customer()), "uid-b": _ok(lapsed)}),
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.increment_metric") as metric,
    ):
        await _deliver(monkeypatch, _transfer("uid-a", "uid-b"), database)

    assert target_row.status == "expired"
    (downgrade,) = _metrics(metric, "subscription_access.downgrade")
    assert downgrade.kwargs["attributes"]["expected"] == "false"
    assert [call.args[0] for call in captured.call_args_list] == [
        "RevenueCat sync removed access on an unexpected event"
    ]


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
@pytest.mark.parametrize(
    "source_read", [_fails(429), _raises(RuntimeError("boom"))], ids=["429", "error"]
)
async def test_a_transfer_source_left_unresolved_is_resolved_by_its_empty_customer(
    monkeypatch, source_read
):
    """The source's read fails during the TRANSFER, so its row is only marked.
    The source is remembered, so the next read of its (now empty) customer --
    a refresh, a repair -- applies the transfer instead of answering
    absent_with_history for good."""
    source = _user("user-a", "uid-a")
    target = _user("user-b", "uid-b")
    store_row = _row(source)
    database = _Database([source, target], [store_row])
    transfer = _transfer("uid-a", "uid-b")

    with _revenuecat({"uid-a": source_read, "uid-b": _ok(_annual())}):
        await _deliver(monkeypatch, transfer, database)

    moved_at = datetime.fromtimestamp(transfer["event_timestamp_ms"] / 1000, UTC)
    assert store_row.status == "active"
    assert store_row.provider_sync_required_at == moved_at
    assert store_row.provider_transfer_source_at == moved_at

    with _revenuecat({"uid-a": _ok(empty_customer_response(app_user_id="uid-a"))}):
        snapshot = await RevenueCatAdapter(api_key="key").get_verified_snapshot("uid-a")
    outcome = await sync_revenuecat_access(
        database, source, snapshot=snapshot, source="refresh"
    )

    assert outcome.result == "applied"
    assert store_row.status == "expired"
    assert store_row.provider_sync_required_at is None
    assert store_row.provider_transfer_source_at is None
    assert evaluate_subscription_access([store_row]).has_access is False


@pytest.mark.asyncio
async def test_a_failed_non_transfer_read_never_makes_absence_authoritative(
    monkeypatch,
):
    """Only a TRANSFER source is remembered; a user whose RENEWAL read failed
    still gets absent_with_history from an empty customer."""
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])

    with _revenuecat({"uid-u": _fails(503)}):
        await _deliver(monkeypatch, _event("RENEWAL", "uid-u"), database)

    assert stored.provider_sync_required_at is not None
    assert stored.provider_transfer_source_at is None
    with _revenuecat({"uid-u": _ok(empty_customer_response(app_user_id="uid-u"))}):
        snapshot = await RevenueCatAdapter(api_key="key").get_verified_snapshot("uid-u")
    with pytest.raises(RevenueCatSnapshotUnavailable) as error:
        await sync_revenuecat_access(
            database, user, snapshot=snapshot, source="refresh"
        )
    assert error.value.reason == "absent_with_history"
    assert stored.status == "active"


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


@pytest.mark.asyncio
async def test_unsupported_store_in_the_snapshot_keeps_the_purchase_effects(
    monkeypatch,
):
    """The read succeeds but the sync refuses the customer: the referral
    credit, row and lifecycle event stand, the webhook ACKs, access is left
    unresolved and the contract problem goes to Sentry."""
    user = _user("user-u", "uid-u")
    database = _Database([user])
    referrals = _pending_referral()
    publisher = MagicMock(publish=AsyncMock())
    body = _annual()
    body["subscriber"]["subscriptions"]["annual"]["store"] = "amazon"

    with (
        _revenuecat({"uid-u": _ok(body)}),
        patch(f"{SYNC}.capture_exception") as reported,
    ):
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
    (row,) = database.rows_of(user)
    assert row.provider_sync_required_at is not None
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([row])
    assert reported.call_args.kwargs["context"]["failure_kind"] == ("unsupported_store")


@pytest.mark.asyncio
async def test_unexpected_sync_error_keeps_the_event_write_and_acks(monkeypatch):
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])
    event = _event("RENEWAL", "uid-u")
    publisher = MagicMock(publish=AsyncMock())

    with (
        _revenuecat({"uid-u": _ok(_annual())}),
        patch(f"{SYNC}.normalize_standard_access_facts", side_effect=TypeError("boom")),
        patch(f"{SYNC}.capture_exception") as reported,
    ):
        result = await _deliver(monkeypatch, event, database, publisher=publisher)

    assert result == {"status": "success"}
    # The renewal's own write stands, as when RevenueCat cannot be read.
    assert stored.expires_at == datetime.fromtimestamp(
        event["expiration_at_ms"] / 1000, UTC
    )
    assert stored.provider_sync_required_at is not None
    (published,) = publisher.publish.await_args_list
    assert published.args[0]["data"]["lifecycle_type"] == "subscription_renewal"
    error = reported.call_args.args[0]
    assert reported.call_args.kwargs["context"]["failure_kind"] == "unexpected"
    assert isinstance(error.__cause__, TypeError)


@pytest.mark.asyncio
async def test_two_deliveries_with_the_same_read_leave_several_rows_resolved(
    monkeypatch,
):
    """The second apply of one RevenueCat read is not conflicting facts, so a
    user with several RevenueCat rows is never left unresolved by it."""
    user = _user("user-u", "uid-u")
    annual = _row(user, id="u-annual")
    monthly = _row(
        user,
        id="u-monthly",
        product_id="onemonth",
        status="expired",
        expires_at=datetime.now(UTC) - timedelta(days=200),
        purchased_at=datetime.now(UTC) - timedelta(days=400),
    )
    database = _Database([user], [annual, monthly])
    body = _annual()

    with (
        _revenuecat({"uid-u": _ok(body)}),
        patch(f"{SYNC}.capture_message") as captured,
    ):
        for _ in range(2):
            result = await _deliver(monkeypatch, _event("RENEWAL", "uid-u"), database)
            assert result == {"status": "success"}

    assert [row.provider_sync_required_at for row in (annual, monthly)] == [None, None]
    assert evaluate_subscription_access([annual, monthly]).has_access is True
    captured.assert_not_called()


def _deleted_account(user_id: str, old_uid: str) -> tuple[User, Subscription]:
    """What account deletion leaves: a tombstoned uid, the RevenueCat row kept."""
    user = User(id=user_id, firebase_uid=f"deleted:{user_id}", is_active=False)
    return user, _row(user, revenuecat_subscriber_id=old_uid)


@pytest.mark.asyncio
async def test_event_for_a_deleted_account_keeps_legacy_effects_but_is_not_synced(
    monkeypatch,
):
    """The store subscription outlives the account: its events still resolve
    the deleted user through the kept row, but there is no access to sync
    and nothing to report."""
    user, row = _deleted_account("user-gone", "uid-gone")
    database = _Database([user], [row])
    referrals = _Referrals(
        ReferralConversion(
            referrer_user_id="referrer-1",
            referred_user_id="user-gone",
            code_used="ABC123",
            status="converted",
            commission_amount=2.0,
            commission_currency="USD",
            commission_amount_vnd=50000,
        )
    )
    referrals.balance = 80000

    with (
        _revenuecat({}) as revenuecat,
        patch(f"{SYNC}.capture_message") as captured,
        patch(f"{SYNC}.capture_exception") as reported,
        patch.object(webhooks, "increment_metric") as processed,
    ):
        result = await _deliver(
            monkeypatch,
            _event("CANCELLATION", "uid-gone", cancel_reason="CUSTOMER_SUPPORT"),
            database,
            referrals,
        )

    assert result == {"status": "success"}
    revenuecat.assert_not_awaited()
    assert row.provider_sync_required_at is None
    captured.assert_not_called()
    reported.assert_not_called()
    # The legacy cancellation still runs on the kept row; a refund claws
    # back no commission (production behaviour, clawback disabled).
    assert row.status == "cancelled"
    assert (referrals.conversion.status, referrals.balance) == ("converted", 80000)
    assert processed.call_args.kwargs["attributes"]["result"] == "account_deleted"


@pytest.mark.asyncio
async def test_transfer_from_a_deleted_account_syncs_only_the_live_target(
    monkeypatch,
):
    """Delete the account, sign up again, restore: RevenueCat transfers the
    purchase from the old uid to the new one."""
    source, source_row = _deleted_account("user-gone", "uid-gone")
    target = _user("user-b", "uid-b")
    database = _Database([source, target], [source_row])

    with (
        _revenuecat({"uid-b": _ok(_annual())}) as revenuecat,
        patch(f"{SYNC}.capture_message") as captured,
    ):
        result = await _deliver(monkeypatch, _transfer("uid-gone", "uid-b"), database)

    assert result == {"status": "success"}
    assert [call.args[0].rsplit("/", 1)[-1] for call in revenuecat.await_args_list] == [
        "uid-b"
    ]
    assert source_row.provider_sync_required_at is None
    (target_row,) = database.rows_of(target)
    assert evaluate_subscription_access([target_row]).has_access is True
    captured.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_read_error_keeps_the_purchase_effects_and_acks(
    monkeypatch,
):
    """The read runs before the legacy handlers: an error the adapter does not
    translate must not fail every delivery before they run (event loss)."""
    user = _user("user-u", "uid-u")
    database = _Database([user])
    referrals = _pending_referral()
    publisher = MagicMock(publish=AsyncMock())

    with (
        _revenuecat({"uid-u": _raises(RuntimeError("event loop is closed"))}),
        patch(f"{SYNC}.capture_exception") as reported,
    ):
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
    (row,) = database.rows_of(user)
    assert row.provider_sync_required_at is not None
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access([row])
    error = reported.call_args.args[0]
    assert reported.call_args.kwargs["context"]["failure_kind"] == "unexpected"
    assert isinstance(error.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_transfer_with_an_unexpected_read_error_still_syncs_the_other_party(
    monkeypatch,
):
    source = _user("user-a", "uid-a")
    target = _user("user-b", "uid-b")
    store_row = _row(source)
    database = _Database([source, target], [store_row])

    with (
        _revenuecat(
            {
                "uid-a": _raises(RuntimeError("event loop is closed")),
                "uid-b": _ok(_annual()),
            }
        ),
        patch(f"{SYNC}.capture_exception") as reported,
    ):
        result = await _deliver(monkeypatch, _transfer("uid-a", "uid-b"), database)

    assert result == {"status": "success"}
    assert store_row.status == "active"
    assert store_row.provider_sync_required_at is not None
    (target_row,) = database.rows_of(target)
    assert evaluate_subscription_access([target_row]).has_access is True
    assert reported.call_args.kwargs["context"]["failure_kind"] == "unexpected"


@pytest.mark.asyncio
async def test_database_error_in_the_sync_fails_the_delivery_for_a_retry(
    monkeypatch,
):
    """A broken transaction cannot keep anything; RevenueCat must redeliver."""
    user = _user("user-u", "uid-u")
    stored = _row(user)
    database = _Database([user], [stored])

    with (
        _revenuecat({"uid-u": _ok(_annual())}),
        patch(
            "src.api.routes.v1.webhook_access_sync.sync_revenuecat_access",
            AsyncMock(side_effect=OperationalError("UPDATE", {}, Exception("gone"))),
        ),
        pytest.raises(SQLAlchemyError),
    ):
        await _deliver(monkeypatch, _event("RENEWAL", "uid-u"), database)

    assert stored.provider_sync_required_at is None

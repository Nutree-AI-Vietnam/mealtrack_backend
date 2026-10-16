"""Webhook -> inbox -> sync -> projection on real Postgres (NM-680).

Drives the real RevenueCat webhook route with ``ent_ledger_write`` on, the
real AsyncUnitOfWork and repositories, and a fake RevenueCat whose answer is
the customer's current state. Webhook payloads are only triggers, so any
order of (duplicated) deliveries converges on the same projection.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from tests.fixtures.fakes.fake_billing_provider import make_sub, snapshot
from tests.fixtures.fakes.fake_feature_flag_reader import StaticFeatureFlagReader

from src.api.routes.v1 import webhooks
from src.app.services.entitlement_account_service import (
    HINTED_SYNC_LIMIT,
    DelayedResyncScheduler,
    EntitlementAccountService,
    SlidingWindowRateLimiter,
)
from src.app.services.entitlement_backfill_service import EntitlementBackfillService
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.exceptions.billing_exceptions import ProviderUnavailable
from src.domain.model.billing.constants import (
    FLAG_ENT_LEDGER_WRITE,
    FLAG_ENT_RECONCILE,
    IdentityKind,
)
from src.domain.model.billing.snapshot import CustomerSnapshot
from src.domain.ports.billing_provider_port import BillingProviderPort
from src.infra.database.models.billing import (
    BillingProviderEventORM,
    StoreSubscriptionORM,
    UserEntitlementORM,
)
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.repositories.billing import AsyncBillingCustomerIdentityRepository
from src.infra.services.rc_budget import RcBudget
from src.infra.workers.entitlement_worker import EntitlementWorker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECRET = "webhook-secret"
PRODUCT = "nutree_2999_1y_0"


class CurrentStateProvider(BillingProviderPort):
    """RevenueCat stand-in: answers each customer's *current* state."""

    provider = "revenuecat"

    def __init__(self):
        self.state: dict[str, list] = {}
        self.down: set[str] = set()
        self.calls: list[str] = []

    async def fetch_customer(self, customer_id: str) -> CustomerSnapshot:
        self.calls.append(customer_id)
        if customer_id in self.down:
            raise ProviderUnavailable("rc down")
        # Every fetch is newer than the previous one, like a real request.
        await asyncio.sleep(0.001)
        return snapshot(
            customer_id,
            *self.state.get(customer_id, []),
            fetched_at=datetime.now(UTC),
        )


def _sub(customer: str, **overrides):
    """A store row for PRODUCT, paid for 30 days from the *real* clock.

    The services here run on the wall clock (``utc_now``), so ``make_sub``'s
    default expiry (pinned to the fixed unit-test ``NOW``) would lapse once
    the calendar passes it.
    """
    overrides.setdefault("expires_at", datetime.now(UTC) + timedelta(days=30))
    return make_sub(customer, PRODUCT, **overrides)


@pytest.fixture
def real_uow(monkeypatch, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    return AsyncUnitOfWork


@pytest.fixture
def provider():
    return CurrentStateProvider()


@pytest.fixture
def flags():
    return StaticFeatureFlagReader({FLAG_ENT_LEDGER_WRITE: True})


@pytest.fixture
def sync(real_uow, provider):
    return EntitlementSyncService(
        uow_factory=real_uow,
        provider=provider,
        budget=RcBudget(rate_per_second=1000.0, concurrency=8),
    )


@pytest.fixture
def inbox(real_uow, sync):
    return EntitlementInboxService(uow_factory=real_uow, sync_service=sync)


@pytest.fixture(autouse=True)
def webhook_wiring(monkeypatch, flags, inbox):
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    monkeypatch.setattr(webhooks, "_get_feature_flag_reader", lambda: flags)
    monkeypatch.setattr(webhooks, "_get_entitlement_inbox_service", lambda: inbox)
    monkeypatch.setattr(webhooks, "_get_event_publisher", lambda: publisher)
    monkeypatch.setattr(webhooks, "_get_affiliate_handler", lambda: None)
    monkeypatch.setattr(
        webhooks, "record_web_funnel_redemption", AsyncMock(return_value=False)
    )
    with patch.multiple(
        "src.api.routes.v1.webhooks.settings",
        REVENUECAT_WEBHOOK_AUTH_HEADER=SECRET,
        REVENUECAT_WEBHOOK_SECRET=SECRET,
    ):
        yield publisher


async def _user(session, firebase_uid: str, *, active: bool = True) -> str:
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            firebase_uid=firebase_uid,
            email=f"{user_id}@example.test",
            username=user_id[:20],
            password_hash="test",
            is_active=active,
        )
    )
    await session.commit()
    return user_id


def _event(event_id: str, event_type: str, app_user_id: str, **extra) -> dict:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "api_version": "1.0",
        "event": {
            "id": event_id,
            "type": event_type,
            "app_user_id": app_user_id,
            "product_id": PRODUCT,
            "store": "APP_STORE",
            "environment": "PRODUCTION",
            "purchased_at_ms": now_ms,
            "expiration_at_ms": now_ms + 30 * 86_400_000,
            "event_timestamp_ms": now_ms,
            "subscriber_attributes": {"$email": {"value": "secret@x.com"}},
            **extra,
        },
    }


async def _deliver(payload: dict) -> dict:
    request = MagicMock()
    request.json = AsyncMock(return_value=payload)
    request.body = AsyncMock(return_value=repr(payload).encode())
    return await webhooks.revenuecat_webhook(request, authorization=SECRET)


async def _projection(session, user_id: str) -> SimpleNamespace | None:
    """Detached copy of the projection row (later expire_all calls cannot touch it)."""
    session.expire_all()
    row = (
        await session.execute(
            select(UserEntitlementORM).where(UserEntitlementORM.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return SimpleNamespace(
        **{column.key: getattr(row, column.key) for column in row.__table__.columns}
    )


async def _count(session, model, **filters) -> int:
    session.expire_all()
    statement = select(func.count()).select_from(model)
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    return (await session.execute(statement)).scalar_one()


def _semantic(row: SimpleNamespace) -> tuple:
    return (
        row.has_access,
        row.state,
        row.access_until,
        row.will_renew,
        row.product_id,
        row.store,
        row.primary_source,
        row.ever_had_access,
    )


async def test_any_order_of_duplicated_deliveries_converges(
    pg_session, provider, inbox
):
    first = await _user(pg_session, "fb-order-a")
    second = await _user(pg_session, "fb-order-b")
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
    for uid in ("fb-order-a", "fb-order-b"):
        # RevenueCat's current truth: cancelled but paid until `expires`.
        provider.state[uid] = [_sub(uid, expires_at=expires, will_renew=False)]

    for event_type in ("INITIAL_PURCHASE", "RENEWAL", "CANCELLATION"):
        assert await _deliver(_event(f"a-{event_type}", event_type, "fb-order-a")) == {
            "status": "success"
        }
    for event_type in (
        "CANCELLATION",
        "INITIAL_PURCHASE",
        "RENEWAL",
        "RENEWAL",
        "INITIAL_PURCHASE",
    ):
        await _deliver(_event(f"b-{event_type}", event_type, "fb-order-b"))

    a = await _projection(pg_session, first)
    b = await _projection(pg_session, second)
    assert a is not None and b is not None
    assert _semantic(a) == _semantic(b)
    assert a.has_access is True and a.state == "cancelled_active"
    assert a.access_until == expires
    # One store row per user, one inbox row per distinct event id.
    assert await _count(pg_session, StoreSubscriptionORM, user_id=second) == 1
    assert await _count(pg_session, BillingProviderEventORM, user_id=second) == 3
    rows = (
        (
            await pg_session.execute(
                select(BillingProviderEventORM).where(
                    BillingProviderEventORM.user_id == second
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.status for row in rows} == {"processed"}
    assert all("subscriber_attributes" not in row.raw["event"] for row in rows)
    # Legacy dual-write still happened in the same transaction as the inbox row.
    assert await _count(pg_session, Subscription, user_id=second) == 1


async def test_settled_duplicate_does_not_refetch(pg_session, provider):
    await _user(pg_session, "fb-dup")
    provider.state["fb-dup"] = [_sub("fb-dup")]

    await _deliver(_event("dup-1", "RENEWAL", "fb-dup"))
    calls = len(provider.calls)
    second = await _deliver(_event("dup-1", "RENEWAL", "fb-dup"))

    assert second == {"status": "success", "duplicate": True}
    assert len(provider.calls) == calls


async def test_late_expiration_event_never_overrides_current_state(
    pg_session, provider
):
    user_id = await _user(pg_session, "fb-late")
    provider.state["fb-late"] = [_sub("fb-late")]

    await _deliver(_event("late-renewal", "RENEWAL", "fb-late"))
    # An old EXPIRATION arrives late while RevenueCat says active.
    await _deliver(_event("late-expiration", "EXPIRATION", "fb-late"))

    assert (await _projection(pg_session, user_id)).has_access is True


async def test_expiry_is_applied_when_the_provider_reports_it(pg_session, provider):
    user_id = await _user(pg_session, "fb-expire")
    provider.state["fb-expire"] = [_sub("fb-expire")]
    await _deliver(_event("exp-1", "RENEWAL", "fb-expire"))

    provider.state["fb-expire"] = [
        _sub(
            "fb-expire",
            state="expired",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            will_renew=False,
        )
    ]
    await _deliver(_event("exp-2", "EXPIRATION", "fb-expire"))

    row = await _projection(pg_session, user_id)
    assert row.has_access is False and row.state == "expired"
    assert row.ever_had_access is True


async def test_transfer_moves_access_to_the_target(pg_session, provider):
    source = await _user(pg_session, "fb-source")
    target = await _user(pg_session, "fb-target")
    sub = _sub("fb-source")
    provider.state["fb-source"] = [sub]
    await _deliver(_event("t-0", "INITIAL_PURCHASE", "fb-source"))
    assert (await _projection(pg_session, source)).has_access is True

    # RevenueCat moved the purchase: only the target's customer lists it now.
    provider.state["fb-source"] = []
    provider.state["fb-target"] = [sub]
    await _deliver(
        {
            "event": {
                "id": "t-1",
                "type": "TRANSFER",
                "transferred_from": ["fb-source"],
                "transferred_to": ["fb-target"],
            }
        }
    )

    assert (await _projection(pg_session, target)).has_access is True
    assert (await _projection(pg_session, source)).has_access is False
    assert await _count(pg_session, StoreSubscriptionORM) == 1


async def test_unknown_user_404_without_reconcile_and_leaves_no_row(pg_session):
    with pytest.raises(HTTPException) as exc_info:
        await _deliver(_event("u-1", "RENEWAL", "fb-unknown"))

    assert exc_info.value.status_code == 404
    assert await _count(pg_session, BillingProviderEventORM) == 0


async def test_parked_event_is_replayed_after_sign_up_and_synced_by_worker(
    pg_session, provider, flags, real_uow, sync, inbox
):
    flags.set(FLAG_ENT_RECONCILE)
    provider.state["fb-later"] = [_sub("fb-later")]

    response = await _deliver(_event("p-1", "INITIAL_PURCHASE", "fb-later"))

    assert response == {"status": "parked", "reason": "user_not_found"}
    assert await _count(pg_session, BillingProviderEventORM, status="unmatched") == 1

    # The user signs up; /v1/users/sync replays the parked row.
    user_id = await _user(pg_session, "fb-later")
    account = EntitlementAccountService(
        query_service=EntitlementQueryService(
            uow_factory=real_uow, sync_service=sync, flags=flags
        ),
        sync_service=sync,
        inbox_service=inbox,
        flags=flags,
        scheduler=DelayedResyncScheduler(lambda _u: asyncio.sleep(0), delays=()),
        rate_limiter=SlidingWindowRateLimiter(),
        hint_rate_limiter=SlidingWindowRateLimiter(limit=HINTED_SYNC_LIMIT),
    )
    await account.after_user_sync(user_id, "fb-later")
    assert await _count(pg_session, BillingProviderEventORM, status="received") == 1

    worker = EntitlementWorker(
        uow_factory=real_uow, inbox=inbox, syncer=sync, flags=flags
    )
    result = await worker.run_once()

    assert result.outcomes == {"processed": 1}
    assert (await _projection(pg_session, user_id)).has_access is True


async def test_parked_event_redelivered_after_sign_up_is_synced_inline(
    pg_session, provider, flags
):
    flags.set(FLAG_ENT_RECONCILE)
    payload = _event("p-redeliver", "INITIAL_PURCHASE", "fb-redeliver")
    assert (await _deliver(payload))["status"] == "parked"

    user_id = await _user(pg_session, "fb-redeliver")
    provider.state["fb-redeliver"] = [_sub("fb-redeliver")]

    # RevenueCat retries the same event id once the user exists.
    assert await _deliver(payload) == {"status": "success"}

    pg_session.expire_all()
    row = (await pg_session.execute(select(BillingProviderEventORM))).scalar_one()
    assert (row.status, row.park_reason, row.user_id) == ("processed", None, user_id)
    assert provider.calls == ["fb-redeliver"]
    assert (await _projection(pg_session, user_id)).has_access is True


async def test_user_matched_by_a_legacy_subscription_row_is_synced(
    pg_session, provider, flags, inbox
):
    """The route's legacy fallback match is kept on the inbox row."""
    flags.set(FLAG_ENT_RECONCILE)
    anonymous = "$RCAnonymousID:legacy-abc"
    user_id = await _user(pg_session, "fb-legacy-match")
    pg_session.add(
        Subscription(
            id=str(uuid4()),
            user_id=user_id,
            revenuecat_subscriber_id=anonymous,
            product_id=PRODUCT,
            platform="ios",
            status="active",
            purchased_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=3),
        )
    )
    await pg_session.commit()
    provider.state["fb-legacy-match"] = [_sub("fb-legacy-match")]

    assert await _deliver(_event("l-1", "RENEWAL", anonymous)) == {"status": "success"}

    pg_session.expire_all()
    row = (await pg_session.execute(select(BillingProviderEventORM))).scalar_one()
    assert (row.status, row.park_reason, row.user_id) == ("processed", None, user_id)
    assert (await _projection(pg_session, user_id)).has_access is True


async def test_deleted_user_is_parked_and_never_written(pg_session, provider):
    user_id = await _user(pg_session, "fb-deleted", active=False)
    provider.state["fb-deleted"] = [_sub("fb-deleted")]

    response = await _deliver(_event("d-1", "RENEWAL", "fb-deleted"))

    assert response == {"status": "ignored", "reason": "deleted_user"}
    assert await _count(pg_session, Subscription, user_id=user_id) == 0
    assert await _projection(pg_session, user_id) is None
    assert provider.calls == []


async def test_provider_outage_leaves_a_retry_that_the_worker_finishes(
    pg_session, provider, flags, real_uow, sync, inbox
):
    user_id = await _user(pg_session, "fb-outage")
    provider.state["fb-outage"] = [_sub("fb-outage")]
    provider.down.add("fb-outage")

    assert await _deliver(_event("o-1", "INITIAL_PURCHASE", "fb-outage")) == {
        "status": "success"
    }
    pg_session.expire_all()
    row = (await pg_session.execute(select(BillingProviderEventORM))).scalar_one()
    assert row.status == "retry" and row.attempts == 1
    assert row.last_error == "provider_unavailable"

    provider.down.clear()
    flags.set(FLAG_ENT_RECONCILE)
    worker = EntitlementWorker(
        uow_factory=real_uow,
        inbox=inbox,
        syncer=sync,
        flags=flags,
        clock=lambda: datetime.now(UTC) + timedelta(minutes=5),
    )
    result = await worker.run_once()

    assert result.outcomes == {"processed": 1}
    assert (await _projection(pg_session, user_id)).has_access is True


async def test_backfill_candidates_include_legacy_customer_ids(pg_session):
    from scripts.backfill_entitlement_ledger import load_candidates

    user_id = await _user(pg_session, "fb-backfill")
    await _user(pg_session, "fb-gone", active=False)
    lead = str(uuid4())
    user = await pg_session.get(User, user_id)
    user.revenuecat_customer_id = lead
    pg_session.add(
        Subscription(
            id=str(uuid4()),
            user_id=user_id,
            revenuecat_subscriber_id="$RCAnonymousID:abc",
            product_id=PRODUCT,
            platform="ios",
            status="active",
            purchased_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=3),
        )
    )
    await pg_session.commit()

    candidates = await load_candidates(
        pg_session, after_user_id=None, limit=10, now=datetime.now(UTC)
    )

    assert [candidate.user_id for candidate in candidates] == [user_id]
    assert candidates[0].firebase_uid == "fb-backfill"
    assert candidates[0].legacy_customer_ids == (lead, "$RCAnonymousID:abc")
    assert candidates[0].legacy_active is True


# ---- legacy TRANSFER rows and the backfill ----
# Legacy ``handle_transfer`` rewrites ``subscriptions.revenuecat_subscriber_id``
# to the target's Firebase UID and keeps ``user_id`` on the source user; the
# backfill must never treat that id as the source user's legacy customer.

LEGACY_LEAD = "5c3f0a1e-2b7d-4c1a-9e8f-0a1b2c3d4e5f"
LEGACY_ANON = "$RCAnonymousID:legacy-device"


def _legacy_row(user_id: str, subscriber_id: str) -> Subscription:
    now = datetime.now(UTC)
    return Subscription(
        id=str(uuid4()),
        user_id=user_id,
        revenuecat_subscriber_id=subscriber_id,
        product_id=PRODUCT,
        platform="ios",
        status="active",
        purchased_at=now,
        expires_at=now + timedelta(days=3),
    )


async def test_transfer_row_is_not_a_legacy_id_and_backfill_hijacks_nothing(
    pg_session, provider, flags, real_uow, sync
):
    from scripts.backfill_entitlement_ledger import load_candidates

    # Historical, pre-ledger data: X bought, then RC transferred it to Y.
    flags.set(FLAG_ENT_LEDGER_WRITE, False)
    x = await _user(pg_session, "fb-x")
    y = await _user(pg_session, "fb-y")
    provider.state["fb-x"] = [_sub("fb-x")]
    await _deliver(_event("t-1", "INITIAL_PURCHASE", "fb-x"))
    provider.state["fb-x"] = []
    provider.state["fb-y"] = [_sub("fb-y")]
    await _deliver(
        {
            "event": {
                "id": "t-2",
                "type": "TRANSFER",
                "transferred_from": ["fb-x"],
                "transferred_to": ["fb-y"],
            }
        }
    )
    pg_session.expire_all()
    legacy = (await pg_session.execute(select(Subscription))).scalars().all()
    assert [(row.user_id, row.revenuecat_subscriber_id) for row in legacy] == [
        (x, "fb-y")
    ]

    flags.set(FLAG_ENT_LEDGER_WRITE, True)
    candidates = await load_candidates(
        pg_session, after_user_id=None, limit=10, now=datetime.now(UTC)
    )
    candidate_x = next(c for c in candidates if c.user_id == x)
    assert candidate_x.legacy_customer_ids == ()

    service = EntitlementBackfillService(
        uow_factory=real_uow,
        sync_service=sync,
        provider=provider,
        budget=RcBudget(rate_per_second=1000.0, concurrency=8),
    )
    await service.backfill(candidate_x, dry_run=False)
    assert (await _projection(pg_session, x)).has_access is False
    assert "fb-y" not in provider.calls

    # Y's RENEWAL resolves to Y, not to the transfer source.
    await _deliver(_event("t-3", "RENEWAL", "fb-y"))
    pg_session.expire_all()
    renewal = (
        await pg_session.execute(
            select(BillingProviderEventORM).where(
                BillingProviderEventORM.provider_event_id == "t-3"
            )
        )
    ).scalar_one()
    assert renewal.user_id == y and renewal.status == "processed"
    assert (await _projection(pg_session, y)).has_access is True


async def test_load_candidates_keeps_only_unowned_lead_and_anonymous_ids(pg_session):
    from scripts.backfill_entitlement_ledger import load_candidates

    uuid_uid = "6d1f7c2a-0b9e-4e43-8f51-3a2b1c0d9e8f"
    x = await _user(pg_session, "fb-x")
    y = await _user(pg_session, "fb-y")
    uuid_uid_owner = await _user(pg_session, uuid_uid)
    taken = "$RCAnonymousID:taken-by-y"
    pg_session.add_all(
        [
            _legacy_row(x, LEGACY_LEAD),
            _legacy_row(x, LEGACY_ANON),
            _legacy_row(x, "fb-y"),  # a TRANSFER-rewritten row
            _legacy_row(x, uuid_uid),  # a UUID-shaped Firebase UID
            _legacy_row(x, taken),
        ]
    )
    await pg_session.commit()
    await AsyncBillingCustomerIdentityRepository(pg_session).add_alias(
        y, "revenuecat", taken, IdentityKind.ANONYMOUS, seen_at=datetime.now(UTC)
    )
    await pg_session.commit()

    candidates = await load_candidates(
        pg_session, after_user_id=None, limit=10, now=datetime.now(UTC)
    )

    by_user = {c.user_id: c for c in candidates}
    assert sorted(by_user[x].legacy_customer_ids) == sorted([LEGACY_LEAD, LEGACY_ANON])
    assert by_user[x].legacy_active is True
    assert by_user[y].legacy_customer_ids == ()
    assert by_user[uuid_uid_owner].legacy_customer_ids == ()

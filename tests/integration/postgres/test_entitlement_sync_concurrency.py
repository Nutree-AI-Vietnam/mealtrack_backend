"""EntitlementSyncService on real Postgres: advisory locks, concurrent syncs,
stale snapshots and key moves through the real AsyncUnitOfWork (NM-680)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from tests.fixtures.fakes.fake_billing_provider import NOW, make_sub, snapshot

from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.exceptions.billing_exceptions import ProviderUnavailable
from src.domain.model.billing.constants import SyncLane
from src.domain.model.billing.snapshot import CustomerSnapshot
from src.domain.ports.billing_provider_port import BillingProviderPort
from src.domain.ports.feature_flag_reader_port import FeatureFlagReaderPort
from src.infra.database.models.billing import (
    BillingCustomerIdentityORM,
    StoreSubscriptionORM,
    UserEntitlementORM,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.rc_budget import RcBudget

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class GatedProvider(BillingProviderPort):
    """Per-customer queue of snapshots; each fetch can wait on a gate."""

    provider = "revenuecat"

    def __init__(self):
        self.queue: dict[str, list[CustomerSnapshot | Exception]] = {}
        self.gate: asyncio.Event | None = None
        self.started = 0

    async def fetch_customer(self, customer_id: str) -> CustomerSnapshot:
        self.started += 1
        if self.gate is not None:
            await self.gate.wait()
        response = self.queue[customer_id].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FlagsOn(FeatureFlagReaderPort):
    async def is_enabled(self, name: str, default: bool = False) -> bool:
        return name == "ent_read_api"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def real_uow(monkeypatch, async_session_factory):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    return AsyncUnitOfWork


async def _user(session, firebase_uid: str) -> str:
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            firebase_uid=firebase_uid,
            email=f"{user_id}@example.test",
            username=user_id,
            password_hash="test",
        )
    )
    await session.commit()
    return user_id


def _service(uow_factory, provider, clock) -> EntitlementSyncService:
    return EntitlementSyncService(
        uow_factory=uow_factory,
        provider=provider,
        budget=RcBudget(rate_per_second=1000.0, concurrency=8),
        clock=clock,
    )


async def _count(session, model, **filters) -> int:
    statement = select(func.count()).select_from(model)
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    return (await session.execute(statement)).scalar_one()


async def test_two_concurrent_syncs_write_one_row_and_keep_newest(
    pg_session, async_session_factory, real_uow
):
    user_id = await _user(pg_session, "fb-race")
    provider = GatedProvider()
    provider.gate = asyncio.Event()
    older = snapshot("fb-race", make_sub("fb-race", will_renew=True), fetched_at=NOW)
    newer = snapshot(
        "fb-race",
        make_sub("fb-race", will_renew=False),
        fetched_at=NOW + timedelta(seconds=5),
    )
    provider.queue["fb-race"] = [newer, older]
    clock = Clock()
    clock.now = NOW + timedelta(seconds=10)
    service = _service(real_uow, provider, clock)

    first = asyncio.create_task(service.sync_user(user_id, SyncLane.WEBHOOK, "a"))
    second = asyncio.create_task(service.sync_user(user_id, SyncLane.WEBHOOK, "b"))
    while provider.started < 2:
        await asyncio.sleep(0.01)
    provider.gate.set()
    results = await asyncio.gather(first, second)

    async with async_session_factory() as check:
        assert await _count(check, StoreSubscriptionORM, user_id=user_id) == 1
        assert await _count(check, UserEntitlementORM, user_id=user_id) == 1
        assert await _count(check, BillingCustomerIdentityORM, user_id=user_id) == 1
        row = (
            await check.execute(
                select(UserEntitlementORM).where(UserEntitlementORM.user_id == user_id)
            )
        ).scalar_one()
    # Whatever the commit order, the newer snapshot wins.
    assert row.last_provider_sync_at == NOW + timedelta(seconds=5)
    assert row.state == "cancelled_active"
    assert sum(result.discarded for result in results) <= 1


async def test_sync_waits_for_the_user_advisory_lock(
    pg_session, async_session_factory, real_uow
):
    user_id = await _user(pg_session, "fb-lock")
    provider = GatedProvider()
    provider.queue["fb-lock"] = [snapshot("fb-lock", make_sub("fb-lock"))]
    service = _service(real_uow, provider, Clock())

    async with async_session_factory() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('ent:' || :u))"),
            {"u": user_id},
        )
        task = asyncio.create_task(service.sync_user(user_id, SyncLane.WEBHOOK, "x"))
        await asyncio.sleep(0.5)
        # Fetched, but blocked on the lock before writing anything.
        assert provider.started == 1
        assert not task.done()
        await holder.commit()
        result = await asyncio.wait_for(task, timeout=10)

    assert result.applied
    assert result.projection.has_access is True


async def test_stale_snapshot_never_overwrites_newer_state(pg_session, real_uow):
    user_id = await _user(pg_session, "fb-stale")
    provider = GatedProvider()
    provider.queue["fb-stale"] = [
        snapshot(
            "fb-stale",
            make_sub("fb-stale", state="expired", expires_at=NOW - timedelta(days=1)),
            fetched_at=NOW + timedelta(minutes=1),
        ),
        snapshot("fb-stale", make_sub("fb-stale"), fetched_at=NOW),
    ]
    clock = Clock()
    clock.now = NOW + timedelta(minutes=2)
    service = _service(real_uow, provider, clock)

    applied = await service.sync_user(user_id, SyncLane.WEBHOOK, "newer")
    discarded = await service.sync_user(user_id, SyncLane.WEBHOOK, "older")

    assert applied.projection.has_access is False
    assert discarded.discarded is True
    assert discarded.projection.has_access is False


async def test_provider_outage_keeps_access_in_database(
    pg_session, async_session_factory, real_uow
):
    user_id = await _user(pg_session, "fb-outage")
    provider = GatedProvider()
    provider.queue["fb-outage"] = [
        snapshot("fb-outage", make_sub("fb-outage")),
        ProviderUnavailable("RevenueCat 503"),
    ]
    clock = Clock()
    service = _service(real_uow, provider, clock)
    await service.sync_user(user_id, SyncLane.WEBHOOK, "ok")
    clock.now = NOW + timedelta(minutes=1)

    result = await service.sync_user(user_id, SyncLane.PURCHASE_SYNC, "down")

    assert result.stale and result.sync_error == "provider_unavailable"
    async with async_session_factory() as check:
        row = (
            await check.execute(
                select(UserEntitlementORM).where(UserEntitlementORM.user_id == user_id)
            )
        ).scalar_one()
    assert row.has_access is True and row.version == 1


async def test_key_move_recomputes_previous_owner(
    pg_session, async_session_factory, real_uow
):
    alice = await _user(pg_session, "fb-alice")
    bob = await _user(pg_session, "fb-bob")
    shared = make_sub("shared")
    provider = GatedProvider()
    provider.queue["fb-alice"] = [snapshot("shared", shared, requested="fb-alice")]
    provider.queue["fb-bob"] = [
        snapshot("shared", shared, requested="fb-bob", fetched_at=NOW + timedelta(1))
    ]
    clock = Clock()
    service = _service(real_uow, provider, clock)
    await service.sync_user(alice, SyncLane.WEBHOOK, "alice")
    clock.now = NOW + timedelta(days=1, minutes=1)

    await service.sync_user(bob, SyncLane.WEBHOOK, "bob")

    async with async_session_factory() as check:
        owners = (await check.execute(select(StoreSubscriptionORM.user_id))).scalars()
        assert list(owners) == [bob]
        projections = {
            row.user_id: row
            for row in (await check.execute(select(UserEntitlementORM))).scalars()
            if row.user_id in (alice, bob)
        }
    assert projections[bob].has_access is True
    assert projections[alice].has_access is False
    assert projections[alice].version == 2


async def test_read_time_expiry_through_real_uow(pg_session, real_uow):
    user_id = await _user(pg_session, "fb-expiry")
    provider = GatedProvider()
    provider.queue["fb-expiry"] = [
        snapshot(
            "fb-expiry", make_sub("fb-expiry", expires_at=NOW + timedelta(hours=1))
        )
    ]
    clock = Clock()
    service = _service(real_uow, provider, clock)
    synced = await service.sync_user(user_id, SyncLane.WEBHOOK, "ok")
    query = EntitlementQueryService(
        uow_factory=real_uow, sync_service=service, flags=FlagsOn(), clock=clock
    )
    clock.now = NOW + timedelta(hours=2)

    view = await query.get_entitlements(user_id)

    assert synced.projection.has_access is True
    assert view.has_access is False
    assert view.state == "expired"
    assert view.version == synced.projection.version + 1
    assert view.synced_at == datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


async def test_two_users_racing_for_a_new_key_end_consistent(
    pg_session, async_session_factory, real_uow
):
    alice = await _user(pg_session, "fb-race-a")
    bob = await _user(pg_session, "fb-race-b")
    shared = make_sub("shared-race")
    provider = GatedProvider()
    provider.gate = asyncio.Event()
    provider.queue["fb-race-a"] = [
        snapshot("shared-race", shared, requested="fb-race-a", fetched_at=NOW)
    ]
    provider.queue["fb-race-b"] = [
        snapshot(
            "shared-race",
            shared,
            requested="fb-race-b",
            fetched_at=NOW + timedelta(seconds=1),
        )
    ]
    clock = Clock()
    clock.now = NOW + timedelta(seconds=2)
    service = _service(real_uow, provider, clock)

    tasks = [
        asyncio.create_task(service.sync_user(alice, SyncLane.WEBHOOK, "a")),
        asyncio.create_task(service.sync_user(bob, SyncLane.WEBHOOK, "b")),
    ]
    while provider.started < 2:
        await asyncio.sleep(0.01)
    provider.gate.set()
    await asyncio.gather(*tasks)

    async with async_session_factory() as check:
        owners = list(
            (
                await check.execute(
                    select(StoreSubscriptionORM.user_id).where(
                        StoreSubscriptionORM.source_customer_id == "shared-race"
                    )
                )
            ).scalars()
        )
        access = {
            row.user_id: row.has_access
            for row in (await check.execute(select(UserEntitlementORM))).scalars()
            if row.user_id in (alice, bob)
        }
    # The newer snapshot owns the key and only its user keeps access.
    assert owners == [bob]
    assert access == {alice: False, bob: True}

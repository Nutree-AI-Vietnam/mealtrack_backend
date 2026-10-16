"""EntitlementWorker passes with in-memory fakes, and the ``--once`` runner."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from tests.fixtures.fakes.fake_billing_provider import (
    NOW,
    FakeBillingProvider,
    FakeBudget,
    FakeClock,
    make_sub,
    make_user,
    snapshot,
)
from tests.fixtures.fakes.fake_feature_flag_reader import StaticFeatureFlagReader
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.exceptions.billing_exceptions import ProviderUnavailable
from src.domain.model.billing.constants import (
    FLAG_ENT_RECONCILE,
    ProviderEventStatus,
    SyncLane,
)
from src.domain.model.billing.ledger import BillingProviderEvent
from src.infra.workers import entitlement_worker as worker_module
from src.infra.workers.entitlement_worker import EntitlementWorker

pytestmark = pytest.mark.asyncio

U1, U2 = "user-1", "user-2"
UID1, UID2 = "uid-1", "uid-2"


class Monotonic:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


@pytest.fixture
def uow():
    store = FakeUnitOfWork()
    store.users.users[U1] = make_user(U1, UID1)
    store.users.users[U2] = make_user(U2, UID2)
    return store


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def provider():
    fake = FakeBillingProvider()
    fake.responses[UID1] = snapshot(UID1, make_sub(UID1))
    fake.responses[UID2] = snapshot(UID2, make_sub(UID2))
    return fake


@pytest.fixture
def budget():
    return FakeBudget()


@pytest.fixture
def flags():
    return StaticFeatureFlagReader({FLAG_ENT_RECONCILE: True})


@pytest.fixture
def sync(uow, provider, budget, clock):
    return EntitlementSyncService(
        uow_factory=lambda: uow, provider=provider, budget=budget, clock=clock
    )


@pytest.fixture
def monotonic():
    return Monotonic()


@pytest.fixture
def worker(uow, sync, flags, clock, monotonic):
    return EntitlementWorker(
        uow_factory=lambda: uow,
        inbox=EntitlementInboxService(
            uow_factory=lambda: uow, sync_service=sync, clock=clock
        ),
        syncer=sync,
        flags=flags,
        worker_id="worker-test",
        clock=clock,
        monotonic=monotonic,
    )


def _event(row_id, app_user_id, *, status="received", **changes):
    return BillingProviderEvent(
        id=row_id,
        provider="revenuecat",
        provider_event_id=f"evt-{row_id}",
        event_type="RENEWAL",
        status=status,
        received_at=NOW,
        app_user_id=app_user_id,
        raw={"event": {"type": "RENEWAL", "app_user_id": app_user_id}},
        **changes,
    )


async def test_idle_while_reconcile_flag_is_off(worker, flags, uow, provider):
    flags.set(FLAG_ENT_RECONCILE, False)
    uow.billing_events.rows["r1"] = _event("r1", UID1)

    result = await worker.run_once(force_periodic=True)

    assert result.enabled is False and not result.worked
    assert uow.billing_events.rows["r1"].status == "received"
    assert provider.calls == []


async def test_processes_due_rows_on_the_reconcile_lane(worker, uow, budget):
    uow.billing_events.rows["r1"] = _event("r1", UID1)
    uow.billing_events.rows["r2"] = _event(
        "r2", UID2, status="retry", next_attempt_at=NOW + timedelta(minutes=5)
    )

    result = await worker.run_once()

    assert result.claimed == 1
    assert result.outcomes == {"processed": 1}
    assert uow.billing_events.rows["r1"].status == ProviderEventStatus.PROCESSED
    assert uow.billing_events.rows["r2"].status == "retry"  # not due yet
    assert budget.lanes == [SyncLane.RECONCILE]


async def test_failures_back_off_and_go_dead_after_ten_attempts(
    worker, uow, provider, clock
):
    provider.responses[UID1] = ProviderUnavailable("down")
    uow.billing_events.rows["r1"] = _event("r1", UID1)

    for _ in range(10):
        row = uow.billing_events.rows["r1"]
        if row.next_attempt_at is not None:
            clock.now = row.next_attempt_at
        await worker.run_once()

    row = uow.billing_events.rows["r1"]
    assert row.status == ProviderEventStatus.DEAD
    assert row.attempts == 10
    assert (await worker.run_once()).claimed == 0


async def test_expired_lease_is_reclaimed(worker, uow, clock):
    uow.billing_events.rows["r1"] = _event(
        "r1",
        UID1,
        status="processing",
        lease_owner="crashed",
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    assert (await worker.run_once()).outcomes == {"processed": 1}


async def test_periodic_jobs_recompute_and_resync(worker, uow, sync, clock, provider):
    await sync.sync_user(U1, SyncLane.WEBHOOK, "seed")
    await sync.sync_user(U2, SyncLane.WEBHOOK, "seed")
    # U2's access ends soon; U1 has access and was synced more than a day ago.
    row = await uow.user_entitlements.get(U2)
    uow.user_entitlements.rows[(U2, "standard")] = replace(
        row, next_change_at=NOW + timedelta(minutes=1)
    )
    clock.now = NOW + timedelta(hours=25)
    provider.calls.clear()

    result = await worker.run_once(force_periodic=True)

    assert result.recomputed == 1  # U2: DB only
    assert result.resynced == 2  # both had access and stale syncs
    assert sorted(provider.calls) == [UID1, UID2]


def _single_resync_worker(uow, sync, flags, clock, monotonic):
    return EntitlementWorker(
        uow_factory=lambda: uow,
        inbox=EntitlementInboxService(
            uow_factory=lambda: uow, sync_service=sync, clock=clock
        ),
        syncer=sync,
        flags=flags,
        periodic_batch_size=1,
        resync_failure_backoff_seconds=3600.0,
        clock=clock,
        monotonic=monotonic,
    )


async def _seed_stale_access(sync, clock):
    """U1 then U2 synced with access; a day later U1 is the stalest."""
    await sync.sync_user(U1, SyncLane.WEBHOOK, "seed")
    clock.now = NOW + timedelta(minutes=1)
    await sync.sync_user(U2, SyncLane.WEBHOOK, "seed")
    clock.now = NOW + timedelta(hours=25)


async def test_deleted_account_never_blocks_the_stale_resync(
    uow, sync, flags, clock, monotonic, provider
):
    await _seed_stale_access(sync, clock)
    uow.users.users[U1] = make_user(U1, UID1, active=False)
    provider.calls.clear()
    worker = _single_resync_worker(uow, sync, flags, clock, monotonic)

    result = await worker.run_once(force_periodic=True)

    assert provider.calls == [UID2]
    assert (result.resynced, result.resync_errors) == (1, 0)


async def test_failed_resync_backs_off_so_other_users_are_reached(
    uow, sync, flags, clock, monotonic, provider
):
    await _seed_stale_access(sync, clock)
    provider.responses[UID1] = ProviderUnavailable("down")
    provider.calls.clear()
    worker = _single_resync_worker(uow, sync, flags, clock, monotonic)

    first = await worker.run_once(force_periodic=True)
    monotonic.value = 60.0
    second = await worker.run_once(force_periodic=True)
    monotonic.value = 3601.0
    provider.responses[UID1] = snapshot(UID1, make_sub(UID1))
    third = await worker.run_once(force_periodic=True)

    assert provider.calls == [UID1, UID2, UID1]
    assert (first.resynced, first.resync_errors) == (0, 1)
    assert (second.resynced, second.resync_errors) == (1, 0)
    assert (third.resynced, third.resync_errors) == (1, 0)


async def test_periodic_jobs_wait_for_their_interval(worker, uow, monotonic, sync):
    first = await worker.run_once()
    monotonic.value = 10.0
    with_interval_pending = await worker.run_once()

    assert first.enabled and with_interval_pending.enabled
    assert worker._next_periodic_at == 60.0
    assert worker._next_sweep_at == 900.0


async def test_sweep_requeues_parked_rows_that_now_resolve(worker, uow):
    uow.billing_events.rows["r1"] = _event(
        "r1", UID1, status="unmatched", park_reason="user_not_found"
    )
    uow.billing_events.rows["r2"] = _event(
        "r2", "nobody", status="unmatched", park_reason="user_not_found"
    )

    first = await worker.run_once(force_periodic=True)
    second = await worker.run_once()

    assert first.replayed == 1
    assert uow.billing_events.rows["r1"].status == ProviderEventStatus.PROCESSED
    assert second.claimed == 0 or second.outcomes.get("processed") == 1
    assert uow.billing_events.rows["r2"].status == "unmatched"


def _received(row_id, app_user_id, received_at, *, status="processed"):
    return replace(_event(row_id, app_user_id, status=status), received_at=received_at)


async def test_retention_nulls_bodies_older_than_90_days(worker, uow, monotonic):
    old = NOW - timedelta(days=90, seconds=1)
    rows = uow.billing_events.rows
    rows["old"] = _received("old", UID1, old)
    rows["parked"] = _received("parked", "nobody", old, status="unmatched")
    rows["recent"] = _received("recent", UID1, NOW - timedelta(days=89))

    with patch.object(worker_module, "increment_metric") as metric:
        first = await worker.run_once()
    monotonic.value = 60.0
    rows["late"] = _received("late", UID1, old)
    before_interval = await worker.run_once()

    assert first.raw_purged == 2
    assert rows["old"].raw is None and rows["parked"].raw is None
    assert rows["recent"].raw is not None
    metric.assert_any_call("entitlement.inbox.raw_purged", value=2)
    # Hourly: the next purge waits for its interval.
    assert before_interval.raw_purged == 0 and rows["late"].raw is not None


async def test_retention_purges_oldest_first_in_batches(
    uow, sync, flags, clock, monotonic
):
    worker = EntitlementWorker(
        uow_factory=lambda: uow,
        inbox=EntitlementInboxService(
            uow_factory=lambda: uow, sync_service=sync, clock=clock
        ),
        syncer=sync,
        flags=flags,
        retention_batch_size=2,
        retention_max_batches=2,
        clock=clock,
        monotonic=monotonic,
    )
    rows = uow.billing_events.rows
    for n in range(5):
        rows[f"r{n}"] = _received(f"r{n}", UID1, NOW - timedelta(days=100 + n))

    first = await worker.run_once(force_periodic=True)
    newest_left = rows["r0"].raw
    second = await worker.run_once(force_periodic=True)

    assert (first.raw_purged, second.raw_purged) == (4, 1)
    assert newest_left is not None
    assert all(row.raw is None for row in rows.values())


async def test_run_loop_stops_on_signal(worker, flags):
    flags.set(FLAG_ENT_RECONCILE, False)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_invalid_budget_is_rejected(uow, sync, flags):
    with pytest.raises(ValueError):
        EntitlementWorker(
            uow_factory=lambda: uow,
            inbox=None,
            syncer=sync,
            flags=flags,
            batch_size=0,
        )


async def test_bootstrap_composes_the_worker():
    from src.bootstrap.entitlement_worker import build_entitlement_worker

    worker = build_entitlement_worker(batch_size=3)

    assert isinstance(worker, EntitlementWorker)
    assert worker._batch_size == 3

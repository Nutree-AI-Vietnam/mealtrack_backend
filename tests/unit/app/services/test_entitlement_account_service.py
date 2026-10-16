"""EntitlementAccountService: /v1/me/entitlements and user hooks (NM-680)."""

import asyncio
from datetime import timedelta

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

from src.api.routes.v1.entitlements import entitlements_response
from src.app.services.entitlement_account_service import (
    HINTED_SYNC_LIMIT,
    ClientHint,
    DelayedResyncScheduler,
    EntitlementAccountService,
    SlidingWindowRateLimiter,
)
from src.app.services.entitlement_inbox_service import EntitlementInboxService
from src.app.services.entitlement_query_service import EntitlementQueryService
from src.app.services.entitlement_sync_service import EntitlementSyncService
from src.domain.exceptions.billing_exceptions import ProviderUnavailable
from src.domain.model.billing.constants import (
    FLAG_ENT_LEDGER_WRITE,
    FLAG_ENT_READ_API,
    FLAG_ENT_READ_API_DEFER,
    FLAG_ENT_RECONCILE,
    ProviderEventStatus,
    SyncLane,
)
from src.domain.model.billing.ledger import BillingProviderEvent

pytestmark = pytest.mark.asyncio

U1 = "user-1"
UID1 = "uid-1"


class RecordingScheduler(DelayedResyncScheduler):
    def __init__(self):
        super().__init__(lambda _user_id: asyncio.sleep(0))
        self.scheduled: list[str] = []

    def schedule(self, user_id):
        self.scheduled.append(user_id)
        return None


@pytest.fixture
def uow():
    store = FakeUnitOfWork()
    store.users.users[U1] = make_user(U1, UID1)
    return store


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def provider():
    fake = FakeBillingProvider()
    fake.responses[UID1] = snapshot(UID1, make_sub(UID1))
    return fake


@pytest.fixture
def flags():
    return StaticFeatureFlagReader({FLAG_ENT_READ_API: True})


@pytest.fixture
def scheduler():
    return RecordingScheduler()


@pytest.fixture
def budget():
    return FakeBudget()


@pytest.fixture
def service(uow, provider, flags, scheduler, clock, budget):
    sync = EntitlementSyncService(
        uow_factory=lambda: uow, provider=provider, budget=budget, clock=clock
    )
    return EntitlementAccountService(
        query_service=EntitlementQueryService(
            uow_factory=lambda: uow, sync_service=sync, flags=flags, clock=clock
        ),
        sync_service=sync,
        inbox_service=EntitlementInboxService(
            uow_factory=lambda: uow, sync_service=sync, clock=clock
        ),
        flags=flags,
        scheduler=scheduler,
        rate_limiter=SlidingWindowRateLimiter(),
        hint_rate_limiter=SlidingWindowRateLimiter(limit=HINTED_SYNC_LIMIT),
        clock=clock,
    )


async def test_flags_off_answers_rc_authority_without_fetching(
    service, flags, provider
):
    flags.set(FLAG_ENT_READ_API, False)

    view = await service.sync_entitlements(U1, ClientHint(kind="purchase"))

    assert view.authority == "rc"
    assert view.state == "unknown"
    assert provider.calls == []


async def test_defer_kill_switch_forces_rc_authority(service, flags, provider):
    flags.set(FLAG_ENT_READ_API_DEFER, True)

    assert (await service.get_entitlements(U1)).authority == "rc"
    assert (await service.sync_entitlements(U1)).authority == "rc"
    assert provider.calls == []


async def test_hinted_sync_always_fetches_on_purchase_lane_and_schedules(
    service, provider, scheduler, budget
):
    first = await service.sync_entitlements(U1, ClientHint(kind="purchase"))
    second = await service.sync_entitlements(U1, ClientHint(kind="restore"))

    assert first.has_access and second.has_access
    assert provider.calls == [UID1, UID1]
    assert budget.lanes == [SyncLane.PURCHASE_SYNC, SyncLane.PURCHASE_SYNC]
    assert scheduler.scheduled == [U1, U1]
    assert second.last_purchase_hint_at == NOW


async def test_hinted_negative_is_an_unconfirmed_negative(service, provider):
    provider.responses[UID1] = snapshot(UID1)

    view = await service.sync_entitlements(U1, ClientHint(kind="purchase"))

    assert not view.has_access
    assert view.is_unconfirmed_negative(NOW, timedelta(minutes=15))
    assert not view.is_unconfirmed_negative(
        NOW + timedelta(minutes=16), timedelta(minutes=15)
    )


async def test_unhinted_sync_refetches_only_when_stale(
    service, provider, clock, budget
):
    await service.sync_entitlements(U1)  # never synced: fetch
    clock.advance(minutes=10)
    await service.sync_entitlements(U1)  # fresh: no fetch
    clock.advance(minutes=6)
    await service.sync_entitlements(U1)  # older than 15 min: fetch

    assert provider.calls == [UID1, UID1]
    assert budget.lanes == [SyncLane.LOGIN_SYNC, SyncLane.LOGIN_SYNC]


async def test_provider_unavailable_returns_last_known_state_as_stale(
    service, provider, clock
):
    await service.sync_entitlements(U1)
    provider.responses[UID1] = ProviderUnavailable("down")
    clock.advance(minutes=1)

    view = await service.sync_entitlements(U1, ClientHint(kind="purchase"))

    assert view.has_access  # never reduced by a provider error
    assert view.stale is True
    assert view.sync_error == "provider_unavailable"
    assert view.authority == "backend"


async def test_provider_unavailable_before_first_sync_is_unknown(service, provider):
    provider.responses[UID1] = ProviderUnavailable("down")

    view = await service.sync_entitlements(U1)

    assert view.state == "unknown"
    assert view.stale and view.sync_error == "provider_unavailable"


async def test_hints_past_their_budget_lose_priority_and_follow_ups(
    service, provider, scheduler, budget
):
    for _ in range(HINTED_SYNC_LIMIT + 6):
        await service.sync_entitlements(U1, ClientHint(kind="purchase"))

    # Two on the purchase lane with follow-ups, then the unhinted budget.
    assert (
        budget.lanes
        == [SyncLane.PURCHASE_SYNC] * HINTED_SYNC_LIMIT + [SyncLane.LOGIN_SYNC] * 6
    )
    assert scheduler.scheduled == [U1] * HINTED_SYNC_LIMIT
    provider.calls.clear()

    view = await service.sync_entitlements(U1, ClientHint(kind="purchase"))

    assert provider.calls == []
    assert view.has_access and not view.stale


async def test_a_hint_after_resumes_still_fetches_and_schedules(
    service, provider, scheduler, clock, budget
):
    """Unhinted calls answered from a fresh projection use no fetch budget."""
    provider.responses[UID1] = snapshot(UID1)  # not subscribed yet
    for _ in range(6):  # logIn + resumes while the payment sheet is open
        await service.sync_entitlements(U1)
        clock.advance(seconds=5)
    assert provider.calls == [UID1]

    provider.responses[UID1] = snapshot(UID1, make_sub(UID1), fetched_at=clock.now)
    view = await service.sync_entitlements(U1, ClientHint(kind="purchase"))

    assert provider.calls == [UID1, UID1]
    assert budget.lanes[-1] == SyncLane.PURCHASE_SYNC
    assert scheduler.scheduled == [U1]
    assert view.has_access and view.last_purchase_hint_at == clock.now


async def test_a_rate_limited_hint_still_records_the_hint(
    service, provider, scheduler, clock
):
    provider.responses[UID1] = snapshot(UID1)
    await service.sync_entitlements(U1)  # projection exists, no access
    clock.advance(minutes=16)
    for _ in range(HINTED_SYNC_LIMIT + 6):
        await service.sync_entitlements(U1, ClientHint(kind="restore"))
    calls = len(provider.calls)
    clock.advance(seconds=1)

    view = await service.sync_entitlements(U1, ClientHint(kind="restore"))

    assert len(provider.calls) == calls  # both budgets spent: no fetch
    assert view.has_access is False
    assert view.last_purchase_hint_at == clock.now
    assert view.is_unconfirmed_negative(clock.now, timedelta(minutes=15))
    response = entitlements_response(view, if_none_match=None)
    assert response.headers["Cache-Control"] == "no-store"
    assert "etag" not in response.headers


async def test_unhinted_fetches_are_limited_per_minute(service, provider, budget):
    provider.responses[UID1] = ProviderUnavailable("down")  # never fresh

    for _ in range(8):
        view = await service.sync_entitlements(U1)

    assert len(provider.calls) == 6
    assert view.state == "unknown"


async def test_follow_ups_run_below_the_webhook_lane(service, budget):
    await service.follow_up_sync(U1)

    assert budget.lanes == [SyncLane.LOGIN_SYNC]


async def test_get_entitlements_never_fetches(service, provider):
    view = await service.get_entitlements(U1)

    assert view.state == "unknown" and view.authority == "backend"
    assert provider.calls == []


# ---- user endpoints ----


async def test_after_user_sync_does_nothing_with_flags_off(service, uow, flags):
    await service.after_user_sync(U1, UID1)

    assert uow.billing_identities.rows == {}


async def test_after_user_sync_seeds_identity_and_replays_when_reconciling(
    service, uow, flags
):
    flags.set(FLAG_ENT_RECONCILE)
    uow.billing_events.rows["e1"] = BillingProviderEvent(
        id="e1",
        provider="revenuecat",
        provider_event_id="evt-1",
        event_type="RENEWAL",
        status=ProviderEventStatus.UNMATCHED,
        received_at=NOW,
        app_user_id=UID1,
        park_reason="user_not_found",
    )

    await service.after_user_sync(U1, UID1)

    assert (await uow.billing_identities.get_primary(U1)).provider_customer_id == UID1
    assert uow.billing_events.rows["e1"].status == ProviderEventStatus.RECEIVED


async def test_after_user_sync_with_ledger_write_only_seeds_identity(
    service, uow, flags
):
    flags.set(FLAG_ENT_LEDGER_WRITE)
    uow.billing_events.rows["e1"] = BillingProviderEvent(
        id="e1",
        provider="revenuecat",
        provider_event_id="evt-1",
        event_type="RENEWAL",
        status=ProviderEventStatus.UNMATCHED,
        received_at=NOW,
        app_user_id=UID1,
    )

    await service.after_user_sync(U1, UID1)

    assert await uow.billing_identities.get_primary(U1) is not None
    assert uow.billing_events.rows["e1"].status == ProviderEventStatus.UNMATCHED


async def test_after_user_sync_never_raises(service, uow, flags, monkeypatch):
    flags.set(FLAG_ENT_LEDGER_WRITE)

    async def boom(*args, **kwargs):
        raise RuntimeError("db")

    monkeypatch.setattr(uow.billing_identities, "upsert_primary", boom)

    await service.after_user_sync(U1, UID1)


async def test_has_subscription_uses_legacy_until_backend_is_authority(service, flags):
    flags.set(FLAG_ENT_READ_API, False)
    assert await service.has_subscription(U1, legacy=True) is True


async def test_has_subscription_reads_projection_and_falls_back_when_unknown(
    service,
):
    # Never synced: the ledger cannot say "no", the legacy value stands.
    assert await service.has_subscription(U1, legacy=True) is True
    await service.sync_entitlements(U1)
    assert await service.has_subscription(U1, legacy=False) is True


async def test_has_subscription_projection_overrides_a_legacy_yes(service, provider):
    provider.responses[UID1] = snapshot(UID1)
    await service.sync_entitlements(U1)

    assert await service.has_subscription(U1, legacy=True) is False


# ---- helpers ----


def test_rate_limiter_sliding_window():
    limiter = SlidingWindowRateLimiter(limit=2, window=timedelta(minutes=1))
    assert limiter.allow("u", NOW)
    assert limiter.allow("u", NOW + timedelta(seconds=1))
    assert not limiter.allow("u", NOW + timedelta(seconds=2))
    assert limiter.allow("other", NOW)
    assert limiter.allow("u", NOW + timedelta(seconds=61))


def test_rate_limiter_bounds_memory():
    limiter = SlidingWindowRateLimiter(limit=1, max_keys=4)
    for index in range(20):
        limiter.allow(f"u{index}", NOW)
    assert len(limiter._hits) <= 4


async def test_scheduler_runs_follow_ups_at_their_delays():
    runs: list[tuple[str, float]] = []
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    async def run(user_id):
        runs.append((user_id, sum(slept)))

    scheduler = DelayedResyncScheduler(run, delays=(5.0, 30.0), sleep=fake_sleep)
    task = scheduler.schedule(U1)
    await task

    assert runs == [(U1, 5.0), (U1, 30.0)]
    assert scheduler.pending() == 0


async def test_scheduler_keeps_one_chain_per_cooldown_and_survives_errors():
    gate = asyncio.Event()
    runs: list[str] = []
    monotonic = [0.0]

    async def gated_sleep(_seconds):
        await gate.wait()

    async def run(user_id):
        runs.append(user_id)
        raise RuntimeError("provider down")

    scheduler = DelayedResyncScheduler(
        run,
        delays=(5.0,),
        sleep=gated_sleep,
        cooldown_seconds=120.0,
        clock=lambda: monotonic[0],
    )
    first = scheduler.schedule(U1)
    assert scheduler.schedule(U1) is None  # a hint while a chain runs
    gate.set()
    await first
    monotonic[0] = 60.0
    assert scheduler.schedule(U1) is None  # within the cooldown
    monotonic[0] = 121.0
    third = scheduler.schedule(U1)
    await third

    assert not first.cancelled()
    assert runs == [U1, U1]

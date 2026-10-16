"""RcBudget: token bucket, priority lanes, concurrency and circuit breaker."""

import asyncio

import pytest

from src.domain.exceptions.billing_exceptions import (
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUnavailable,
)
from src.domain.model.billing.constants import SyncLane
from src.infra.services.rc_budget import RcBudget

pytestmark = pytest.mark.asyncio


class ManualClock:
    """Monotonic clock advanced only by the budget's own sleeps."""

    def __init__(self):
        self.now = 1000.0
        # While frozen, sleeping waiters yield without time passing.
        self.frozen = False

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if not self.frozen:
            self.now += seconds
        await asyncio.sleep(0)


def _budget(clock: ManualClock, **kwargs) -> RcBudget:
    defaults = {"rate_per_second": 1.0, "concurrency": 4, "poll_interval": 0.01}
    defaults.update(kwargs)
    return RcBudget(clock=clock, sleep=clock.sleep, **defaults)


async def _use(budget: RcBudget, lane: str, timeout: float | None = None):
    async with budget.acquire(lane, timeout):
        pass


async def test_burst_then_paced_by_rate():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=2.0)
    start = clock.now

    for _ in range(4):
        await _use(budget, SyncLane.WEBHOOK)

    # Burst of 2 tokens, then one every 0.5 s.
    assert clock.now - start == pytest.approx(1.0, abs=0.05)


async def test_higher_priority_lane_is_served_first():
    clock = ManualClock()
    budget = _budget(clock)
    await _use(budget, SyncLane.WEBHOOK)  # drain the only token
    order: list[str] = []

    async def worker(lane: str):
        async with budget.acquire(lane, timeout=60):
            order.append(lane)

    # Queue the lanes lowest priority first while no token can arrive.
    clock.frozen = True
    tasks = []
    for lane in (SyncLane.BACKFILL, SyncLane.RECONCILE, SyncLane.PURCHASE_SYNC):
        tasks.append(asyncio.create_task(worker(lane)))
        for _ in range(3):
            await asyncio.sleep(0)
    assert order == []
    clock.frozen = False
    await asyncio.gather(*tasks)

    assert order == [SyncLane.PURCHASE_SYNC, SyncLane.RECONCILE, SyncLane.BACKFILL]


async def test_wait_timeout_raises_rate_limited():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=0.1)
    await _use(budget, SyncLane.BACKFILL)

    with pytest.raises(ProviderRateLimited):
        await _use(budget, SyncLane.WEBHOOK, timeout=1.0)


async def test_lane_default_timeout_applies():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=0.01)
    await _use(budget, SyncLane.BACKFILL)
    start = clock.now

    with pytest.raises(ProviderRateLimited):
        await _use(budget, SyncLane.WEBHOOK)

    assert clock.now - start == pytest.approx(3.0, abs=0.05)


async def test_concurrency_limit_holds_slots():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=100.0, burst=10, concurrency=1)
    release = asyncio.Event()
    entered: list[str] = []

    async def holder():
        async with budget.acquire(SyncLane.WEBHOOK):
            entered.append("holder")
            await release.wait()

    async def second():
        async with budget.acquire(SyncLane.WEBHOOK, timeout=60):
            entered.append("second")

    first_task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    second_task = asyncio.create_task(second())
    for _ in range(5):
        await asyncio.sleep(0)
    assert entered == ["holder"]
    assert budget.in_flight == 1
    release.set()
    await asyncio.gather(first_task, second_task)
    assert entered == ["holder", "second"]
    assert budget.in_flight == 0


async def test_circuit_opens_after_consecutive_unavailable_and_recovers():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=100.0, burst=100, failure_threshold=5)

    for _ in range(5):
        with pytest.raises(ProviderUnavailable):
            async with budget.acquire(SyncLane.WEBHOOK):
                raise ProviderUnavailable("down")

    assert budget.circuit_open is True
    with pytest.raises(ProviderUnavailable, match="circuit"):
        await _use(budget, SyncLane.PURCHASE_SYNC)

    clock.now += 30.0
    assert budget.circuit_open is False
    # Half-open: one probe succeeds and closes the circuit.
    await _use(budget, SyncLane.WEBHOOK)
    await _use(budget, SyncLane.WEBHOOK)
    assert budget.circuit_open is False


async def test_failed_probe_reopens_circuit():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=100.0, burst=100, failure_threshold=1)
    with pytest.raises(ProviderUnavailable):
        async with budget.acquire(SyncLane.WEBHOOK):
            raise ProviderUnavailable("down")
    clock.now += 30.0

    with pytest.raises(ProviderUnavailable):
        async with budget.acquire(SyncLane.WEBHOOK):
            raise ProviderUnavailable("still down")

    assert budget.circuit_open is True


async def test_not_found_counts_as_reachable():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=100.0, burst=100, failure_threshold=2)
    with pytest.raises(ProviderUnavailable):
        async with budget.acquire(SyncLane.WEBHOOK):
            raise ProviderUnavailable("blip")
    with pytest.raises(ProviderNotFound):
        async with budget.acquire(SyncLane.WEBHOOK):
            raise ProviderNotFound("nobody")
    with pytest.raises(ProviderUnavailable):
        async with budget.acquire(SyncLane.WEBHOOK):
            raise ProviderUnavailable("blip")

    assert budget.circuit_open is False


async def test_rate_limited_response_pauses_every_lane():
    clock = ManualClock()
    budget = _budget(clock, rate_per_second=100.0, burst=100)
    with pytest.raises(ProviderRateLimited):
        async with budget.acquire(SyncLane.WEBHOOK):
            raise ProviderRateLimited("429", retry_after_seconds=4.0)
    start = clock.now

    await _use(budget, SyncLane.PURCHASE_SYNC, timeout=10)

    assert clock.now - start == pytest.approx(4.0, abs=0.05)


async def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        RcBudget(rate_per_second=0)
    with pytest.raises(ValueError):
        RcBudget(concurrency=0)


async def test_real_clock_smoke():
    budget = RcBudget(rate_per_second=50.0, concurrency=2)
    async with budget.acquire(SyncLane.LOGIN_SYNC):
        assert budget.in_flight == 1
    assert budget.in_flight == 0

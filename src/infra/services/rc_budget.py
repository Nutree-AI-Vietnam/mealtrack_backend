"""Process-wide RevenueCat REST budget: token bucket, priority lanes,
concurrency limit and circuit breaker (plan §4 item 6, §6.3).

Every entitlement sync shares one budget. Lower-priority lanes wait while a
higher-priority lane is queued. Redis-shared budgets are optional and not used
here: each process gets ``ENTITLEMENT_RC_RPS`` on its own.

Waiting is a short polling loop rather than asyncio primitives, so one instance
is safe to share across event loops (tests, scripts).
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from src.domain.exceptions.billing_exceptions import (
    BillingProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)
from src.domain.model.billing.constants import PROVIDER_REVENUECAT, SyncLane
from src.domain.ports.billing_provider_budget_port import BillingProviderBudgetPort
from src.observability import distribution_metric, increment_metric

logger = logging.getLogger(__name__)

# Lower value = served first.
LANE_PRIORITY: dict[str, int] = {
    SyncLane.PURCHASE_SYNC: 0,
    SyncLane.WEBHOOK: 1,
    SyncLane.LOGIN_SYNC: 2,
    SyncLane.RECONCILE: 3,
    SyncLane.BACKFILL: 4,
}
_UNKNOWN_LANE_PRIORITY = len(LANE_PRIORITY)

# How long a lane waits for a token before reporting "rate limited".
DEFAULT_LANE_TIMEOUTS: dict[str, float] = {
    SyncLane.PURCHASE_SYNC: 5.0,
    SyncLane.WEBHOOK: 3.0,
    SyncLane.LOGIN_SYNC: 5.0,
    SyncLane.RECONCILE: 30.0,
    SyncLane.BACKFILL: 120.0,
}


class RcBudget(BillingProviderBudgetPort):
    def __init__(
        self,
        *,
        rate_per_second: float = 2.0,
        concurrency: int = 4,
        burst: float | None = None,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        rate_limit_pause_seconds: float = 5.0,
        poll_interval: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._rate = rate_per_second
        self._capacity = burst if burst is not None else max(1.0, rate_per_second)
        self._concurrency = concurrency
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._rate_limit_pause = rate_limit_pause_seconds
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep

        self._tokens = self._capacity
        self._refilled_at = clock()
        self._in_flight = 0
        self._waiters: list[tuple[int, int]] = []
        self._sequence = itertools.count()
        self._paused_until = 0.0
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._probe_in_flight = False

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def circuit_open(self) -> bool:
        return self._open_until is not None and self._clock() < self._open_until

    @asynccontextmanager
    async def acquire(
        self, lane: str, timeout: float | None = None
    ) -> AsyncIterator[None]:
        if timeout is None:
            timeout = DEFAULT_LANE_TIMEOUTS.get(lane, 30.0)
        self._fail_fast_if_open()
        started = self._clock()
        await self._wait_turn(
            LANE_PRIORITY.get(lane, _UNKNOWN_LANE_PRIORITY), started + timeout
        )
        distribution_metric(
            "rc.budget.wait",
            (self._clock() - started) * 1000.0,
            unit="millisecond",
            attributes={"lane": str(lane)},
        )
        try:
            is_probe = self._admit()
        except ProviderUnavailable:
            self._refund_token()
            raise
        self._in_flight += 1
        try:
            yield
        except ProviderUnavailable:
            self._record_unavailable(is_probe)
            raise
        except ProviderRateLimited as exc:
            self._record_rate_limited(exc, is_probe)
            raise
        except BillingProviderError:
            # The provider answered (404, unusable body): it is reachable.
            self._record_success(is_probe)
            raise
        except BaseException:
            if is_probe:
                self._probe_in_flight = False
            raise
        else:
            self._record_success(is_probe)
        finally:
            self._in_flight -= 1

    async def _wait_turn(self, priority: int, deadline: float) -> None:
        entry = (priority, next(self._sequence))
        heapq.heappush(self._waiters, entry)
        try:
            while True:
                now = self._clock()
                self._refill(now)
                at_head = self._waiters[0] == entry
                if (
                    at_head
                    and self._tokens >= 1.0
                    and self._in_flight < self._concurrency
                    and now >= self._paused_until
                ):
                    heapq.heappop(self._waiters)
                    self._tokens -= 1.0
                    return
                if now >= deadline:
                    raise ProviderRateLimited(
                        "RevenueCat budget wait timed out",
                        provider=PROVIDER_REVENUECAT,
                    )
                delay = self._poll_interval
                if at_head and now < self._paused_until:
                    delay = self._paused_until - now
                elif at_head and self._tokens < 1.0:
                    delay = (1.0 - self._tokens) / self._rate
                await self._sleep(max(min(delay, deadline - now), 0.001))
        finally:
            if entry in self._waiters:
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)

    def _refill(self, now: float) -> None:
        elapsed = max(now - self._refilled_at, 0.0)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._refilled_at = now

    def _refund_token(self) -> None:
        self._tokens = min(self._capacity, self._tokens + 1.0)

    def _fail_fast_if_open(self) -> None:
        if self._open_until is not None and self._clock() < self._open_until:
            raise ProviderUnavailable(
                "RevenueCat circuit breaker is open", provider=PROVIDER_REVENUECAT
            )

    def _admit(self) -> bool:
        """Return True when this call is the half-open probe."""
        if self._open_until is None:
            return False
        if self._clock() < self._open_until or self._probe_in_flight:
            raise ProviderUnavailable(
                "RevenueCat circuit breaker is open", provider=PROVIDER_REVENUECAT
            )
        self._probe_in_flight = True
        return True

    def _record_success(self, is_probe: bool) -> None:
        if is_probe:
            self._probe_in_flight = False
            logger.info("RevenueCat circuit breaker closed")
        self._open_until = None
        self._consecutive_failures = 0

    def _record_unavailable(self, is_probe: bool) -> None:
        self._consecutive_failures += 1
        if is_probe or self._consecutive_failures >= self._failure_threshold:
            self._probe_in_flight = False
            self._open_until = self._clock() + self._open_seconds
            increment_metric("rc.circuit.open")
            logger.warning(
                "RevenueCat circuit breaker opened for %.0fs after %d failures",
                self._open_seconds,
                self._consecutive_failures,
            )

    def _record_rate_limited(self, exc: ProviderRateLimited, is_probe: bool) -> None:
        if is_probe:
            self._probe_in_flight = False
        increment_metric("rc.429")
        pause = exc.retry_after_seconds or self._rate_limit_pause
        self._paused_until = max(self._paused_until, self._clock() + pause)

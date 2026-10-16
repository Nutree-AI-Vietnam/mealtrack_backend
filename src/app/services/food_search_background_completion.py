"""Run slow food search work once per search shape, past the response if needed.

A search request waits for provider-backed results only up to a budget. The
work keeps running after that so its results still reach the cache, and a
second identical request joins the running work instead of starting another
provider call.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)

SearchResults = list[dict[str, Any]]
_Published = tuple[SearchResults, bool]


class Publish(Protocol):
    """Hand results to waiting requests; ``partial`` marks a degraded page."""

    def __call__(self, results: SearchResults, *, partial: bool = False) -> None: ...


CompletionWork = Callable[[Publish], Awaitable[None]]


class CompletionStatus(Enum):
    READY = "ready"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass(frozen=True)
class CompletionOutcome:
    status: CompletionStatus
    results: SearchResults = field(default_factory=list)
    partial: bool = False


def completion_budget_seconds(*, autocomplete: bool, has_local_results: bool) -> float:
    """Seconds a request waits for provider-backed results.

    With local rows to show the wait is short and a late provider page warms
    the cache for the next keystroke. With nothing local the user would see an
    empty list, so the request waits close to the provider's own timeout.
    """
    if autocomplete:
        return 0.5 if has_local_results else 3.0
    return 0.8 if has_local_results else 8.0


class FoodSearchBackgroundCompletions:
    """Owns background search tasks so they finish, dedupe and drain cleanly."""

    def __init__(self, max_in_flight: int = 256) -> None:
        self._max_in_flight = max_in_flight
        self._in_flight: dict[str, asyncio.Future[_Published | None]] = {}
        self._pending: set[asyncio.Task[None]] = set()

    def start(
        self, key: str, work: CompletionWork, *, name: str
    ) -> asyncio.Future[_Published | None]:
        """Start ``work`` for ``key`` unless it is already running.

        ``work`` receives a ``publish`` callback; the returned future resolves
        with the first published results, or ``None`` when the work ends
        without publishing. Work that already published but is still writing
        caches is joined too, so the repeat request gets the published page.
        """
        loop = asyncio.get_running_loop()
        running = self._in_flight.get(key)
        # A future from another event loop cannot be awaited here (one owner
        # can outlive a loop, e.g. across test runs); start fresh work instead.
        if running is not None and running.get_loop() is loop:
            return running
        ready: asyncio.Future[_Published | None] = loop.create_future()

        def publish(results: SearchResults, *, partial: bool = False) -> None:
            if not ready.done():
                ready.set_result((copy.deepcopy(results), partial))

        async def run() -> None:
            try:
                await work(publish)
            finally:
                if not ready.done():
                    ready.set_result(None)
                if self._in_flight.get(key) is ready:
                    del self._in_flight[key]

        task = loop.create_task(run(), name=name)
        self._pending.add(task)
        task.add_done_callback(self._finished)
        # Past the cap, work still runs; it just cannot be joined.
        if len(self._in_flight) < self._max_in_flight:
            self._in_flight[key] = ready
        return ready

    async def wait(
        self, ready: asyncio.Future[_Published | None], budget: float
    ) -> CompletionOutcome:
        """Wait up to ``budget`` seconds without cancelling the work."""
        try:
            published = await asyncio.wait_for(asyncio.shield(ready), timeout=budget)
        except TimeoutError:
            return CompletionOutcome(CompletionStatus.TIMEOUT)
        if published is None:
            return CompletionOutcome(CompletionStatus.FAILED)
        results, partial = published
        # Joined requests share one result; each gets its own copy.
        return CompletionOutcome(
            CompletionStatus.READY, copy.deepcopy(results), partial=partial
        )

    async def drain(self, timeout: float = 5.0) -> None:
        """Let running work finish, then cancel whatever is left."""
        loop = asyncio.get_running_loop()
        tasks = [task for task in self._pending if task.get_loop() is loop]
        if not tasks:
            return
        _, still_running = await asyncio.wait(tasks, timeout=timeout)
        for task in still_running:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._pending.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "food search background work failed",
                exc_info=(type(error), error, error.__traceback__),
            )

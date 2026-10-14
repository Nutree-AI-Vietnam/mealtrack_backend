"""In-process coordination between the AI job API, worker lanes, and runners."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class AiJobCancelSignals:
    """Delivers a cancel request to an attempt running in this process at once.

    Attempts running in another process notice the request on their next
    heartbeat instead.
    """

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def watch(self, job_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._events[job_id] = event
        return event

    def release(self, job_id: str, event: asyncio.Event) -> None:
        # A newer attempt of the same job may have replaced the entry.
        if self._events.get(job_id) is event:
            del self._events[job_id]

    def signal(self, job_id: str) -> bool:
        event = self._events.get(job_id)
        if event is None:
            return False
        event.set()
        return True


class AiJobWakeup:
    """Wakes idle worker lanes in this process when a job becomes due.

    A lane passes the generation it read before its last claim attempt, so a
    job enqueued between that attempt and the wait is never slept through.
    """

    def __init__(self) -> None:
        self._generation = 0
        self._waiters: set[asyncio.Future[None]] = set()

    @property
    def generation(self) -> int:
        return self._generation

    def notify(self) -> None:
        self._generation += 1
        waiters, self._waiters = self._waiters, set()
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    async def wait(self, timeout: float, *, since: int | None = None) -> bool:
        """Return True when notified, False when the timeout passed first."""
        if since is not None and since != self._generation:
            return True
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.add(waiter)
        try:
            await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            return False
        finally:
            self._waiters.discard(waiter)
        return True


class AiJobsSchemaProbe:
    """Remembers whether the AI job tables are migrated.

    A deploy that ships before its migration reports the feature as
    unavailable instead of failing requests. A positive answer is final; a
    negative one is re-checked after ``retry_after_seconds``.
    """

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        *,
        retry_after_seconds: float = 60.0,
        error_retry_after_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uow_factory = uow_factory
        self._retry_after = retry_after_seconds
        self._error_retry_after = error_retry_after_seconds
        self._clock = clock
        self._ready = False
        self._next_check_at: float | None = None

    async def ready(self) -> bool:
        if self._ready:
            return True
        now = self._clock()
        if self._next_check_at is not None and now < self._next_check_at:
            return False
        try:
            async with self._uow_factory() as uow:
                ready = bool(await uow.ai_jobs.schema_ready())
        except Exception:
            # The database being down says nothing about the migration;
            # check again soon rather than caching a wrong answer.
            logger.warning("AI jobs schema check failed", exc_info=True)
            self._next_check_at = now + self._error_retry_after
            return False
        self._ready = ready
        self._next_check_at = now + self._retry_after
        if not ready:
            logger.warning("AI jobs schema is not migrated; background jobs are off")
        return ready

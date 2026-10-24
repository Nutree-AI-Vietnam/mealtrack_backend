"""Supervision shared by the worker and the in-process chat runner."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from src.domain.model.ai_jobs import AiJobHeartbeat

logger = logging.getLogger(__name__)

# Cleanup writes that must outlive a cancelled attempt; asyncio only keeps
# weak references to tasks, so pending ones are held here.
_DETACHED: set[asyncio.Task[Any]] = set()


class AttemptOutcome(StrEnum):
    FINISHED = "finished"
    LOST_LEASE = "lost_lease"
    CANCELLED = "cancelled"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"


async def supervise_attempt(
    *,
    work: asyncio.Future[Any],
    heartbeat: Callable[[], Awaitable[AiJobHeartbeat | None]],
    cancel_event: asyncio.Event,
    stop: asyncio.Event | None,
    interruptible: bool,
    heartbeat_seconds: float,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> AttemptOutcome:
    """Wait for ``work`` while keeping its lease alive.

    Returns as soon as the work finishes, the lease is lost, or the deadline
    (a ``clock`` reading) passes. Cancel requests and shutdown end the wait
    only for ``interruptible`` work; the rest is left to finish, and the
    repository records a pending cancel when the result is written.
    ``heartbeat`` returns None when the lease could not be checked.

    Work that finishes during a heartbeat counts as finished, even if that
    heartbeat crossed the deadline, lost the lease or saw a cancel request;
    the fenced write of the result decides whether it is kept.
    """
    cancel_waiter = asyncio.ensure_future(cancel_event.wait())
    stop_waiter = asyncio.ensure_future(stop.wait()) if stop is not None else None
    try:
        next_beat = clock() + heartbeat_seconds
        while True:
            if work.done():
                return AttemptOutcome.FINISHED
            now = clock()
            if now >= deadline:
                return AttemptOutcome.TIMED_OUT
            watched: set[asyncio.Future[Any]] = {work}
            if interruptible:
                watched.add(cancel_waiter)
                if stop_waiter is not None:
                    watched.add(stop_waiter)
            done, _ = await asyncio.wait(
                watched,
                timeout=max(0.0, min(next_beat, deadline) - now),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if work in done:
                return AttemptOutcome.FINISHED
            if cancel_waiter in done:
                return AttemptOutcome.CANCELLED
            if stop_waiter is not None and stop_waiter in done:
                return AttemptOutcome.STOPPED
            if clock() < next_beat:
                continue
            beat = await heartbeat()
            next_beat = clock() + heartbeat_seconds
            if beat is None or work.done():
                continue
            if not beat.renewed:
                return AttemptOutcome.LOST_LEASE
            if beat.cancel_requested and interruptible:
                return AttemptOutcome.CANCELLED
    finally:
        cancel_waiter.cancel()
        if stop_waiter is not None:
            stop_waiter.cancel()


async def interrupt_work(work: asyncio.Future[Any], *, timeout: float) -> None:
    """Cancel ``work`` and give its cleanup up to ``timeout`` seconds."""
    if work.done():
        return
    work.cancel()
    await asyncio.wait({work}, timeout=timeout)


async def run_detached(
    awaitable: Awaitable[Any], *, timeout: float, description: str
) -> None:
    """Run a cleanup write that a second cancellation must not interrupt.

    The caller stops waiting when it is cancelled again, but the write keeps
    running in the background. Failures are logged, never raised.
    """
    task = asyncio.ensure_future(_bounded(awaitable, timeout, description))
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    await asyncio.shield(task)


async def _bounded(awaitable: Awaitable[Any], timeout: float, description: str) -> None:
    try:
        async with asyncio.timeout(timeout):
            await awaitable
    except Exception:
        logger.warning("AI job cleanup failed: %s", description, exc_info=True)

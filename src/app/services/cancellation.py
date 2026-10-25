"""Work that must not stop halfway when its caller is cancelled."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# asyncio only keeps weak references to tasks, so unfinished ones are held here.
_RUNNING: set[asyncio.Task[Any]] = set()


async def run_to_completion[T](coro: Coroutine[Any, Any, T], *, description: str) -> T:
    """Await ``coro``; if the caller is cancelled, ``coro`` still finishes.

    The caller sees the cancellation at once while the work carries on in the
    background. Use it to keep a write and the event that announces it
    together: a background job stopped at its deadline must not save a meal
    and skip the meal's created event. A failure that nobody waits for any
    more is logged.
    """
    task = asyncio.ensure_future(coro)
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(lambda done: _log_unobserved_failure(done, description))
        raise


def _log_unobserved_failure(task: asyncio.Task[Any], description: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "%s failed after its caller was cancelled", description, exc_info=exc
        )

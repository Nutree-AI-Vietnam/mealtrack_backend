"""Composition root for server-sent engagement pushes.

With ``ENGAGEMENT_PUSH_ENABLED`` the RevenueCat webhook queues the
payment-failed push and every API process runs a dispatcher; claims use SKIP
LOCKED, so the processes share the outbox safely. With the flag off nothing
is queued, so turning it on never flushes a backlog outside the send window.
"""

from __future__ import annotations

import asyncio
import logging

from src.app.services.engagement_push import EngagementPushDispatcher
from src.infra.adapters.fcm_push_sender import FcmPushSender
from src.infra.concurrency.executors import get_push_executor
from src.infra.config.settings import get_settings
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)

_POLL_SECONDS = 30.0
_BATCH_SIZE = 20
_SHUTDOWN_TIMEOUT_SECONDS = 10.0
_CANCEL_TIMEOUT_SECONDS = 5.0

_stop: asyncio.Event | None = None
_task: asyncio.Task[None] | None = None


def engagement_push_enabled() -> bool:
    return get_settings().ENGAGEMENT_PUSH_ENABLED


def start_engagement_push_dispatcher() -> bool:
    """Start this process's dispatcher; False when off or already running."""
    global _stop, _task
    if not engagement_push_enabled() or _task is not None:
        return False
    dispatcher = EngagementPushDispatcher(
        uow_factory=AsyncUnitOfWork,
        sender=FcmPushSender(executor=get_push_executor()),
        interval_seconds=_POLL_SECONDS,
        batch_size=_BATCH_SIZE,
    )
    stop = asyncio.Event()
    task = asyncio.ensure_future(dispatcher.run(stop))
    task.add_done_callback(_log_exit)
    _stop, _task = stop, task
    logger.info("Engagement push dispatcher started")
    return True


async def stop_engagement_push_dispatcher(
    *, timeout: float = _SHUTDOWN_TIMEOUT_SECONDS
) -> None:
    """Stop the dispatcher; a send in flight gets ``timeout`` to finish."""
    global _stop, _task
    task = _task
    if task is None:
        return
    if _stop is not None:
        _stop.set()
    _, pending = await asyncio.wait({task}, timeout=timeout)
    for unfinished in pending:
        unfinished.cancel()
    if pending:
        await asyncio.wait(pending, timeout=_CANCEL_TIMEOUT_SECONDS)
    _stop, _task = None, None


def _log_exit(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Engagement push dispatcher stopped unexpectedly", exc_info=exc)

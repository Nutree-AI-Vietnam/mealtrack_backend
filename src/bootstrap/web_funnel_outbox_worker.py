"""Drain due web-funnel outbox rows on a timer.

Webhooks only wake the outbox for a paid lead, so timed work such as plan
reminders needs this loop. Rows are claimed with SKIP LOCKED, so every API
worker process can run it without sending twice.
"""

import asyncio
import logging

from src.infra.config.settings import settings
from src.infra.services.web_funnel_outbox_dispatch_service import (
    dispatch_web_funnel_outbox,
)

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
_task: asyncio.Task | None = None


async def _run() -> None:
    while True:
        try:
            await dispatch_web_funnel_outbox()
        except Exception:
            logger.exception("Web-funnel outbox dispatch failed")
        await asyncio.sleep(POLL_SECONDS)


def start_web_funnel_outbox_worker() -> None:
    global _task
    if not settings.WEB_FUNNEL_OUTBOX_WORKER_ENABLED or _task is not None:
        return
    _task = asyncio.create_task(_run(), name="web-funnel-outbox")
    logger.info("Web-funnel outbox worker started")


async def stop_web_funnel_outbox_worker() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None

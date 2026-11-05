"""Deletes finished AI jobs once they are older than the retention window.

Every scan, text parse and week leaves a row holding its input and result.
The app reads a job within minutes and lists only the last 24 hours, so a
finished row is dead weight after that. Queued and running jobs are never
deleted. Each worker process runs one janitor; several at once are safe
because a pass skips rows another transaction holds.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class AiJobJanitor:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        retention_days: int,
        interval_seconds: float = 3600.0,
        first_delay_seconds: float = 60.0,
        batch_size: int = 1000,
        max_batches: int = 50,
        ready: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._retention_days = retention_days
        self._interval = interval_seconds
        self._first_delay = first_delay_seconds
        self._batch_size = batch_size
        # Bounds one pass (50,000 rows by default); the rest waits an hour.
        self._max_batches = max_batches
        self._ready = ready

    async def run(self, stop: asyncio.Event) -> None:
        # Startup is busy enough; the first pass waits a minute.
        if await _stopped_within(stop, self._first_delay):
            return
        while not stop.is_set():
            try:
                if self._ready is None or await self._ready():
                    deleted = await self.run_once()
                    if deleted:
                        logger.info(
                            "Deleted %s finished AI jobs older than %s days",
                            deleted,
                            self._retention_days,
                        )
            except Exception:
                logger.exception("AI job cleanup pass failed")
            if await _stopped_within(stop, self._interval):
                return

    async def run_once(self) -> int:
        """Delete expired finished jobs, one committed batch at a time."""
        total = 0
        for _ in range(self._max_batches):
            async with self._uow_factory() as uow:
                deleted = await uow.ai_jobs.delete_finished(
                    older_than_days=self._retention_days, limit=self._batch_size
                )
            total += deleted
            if deleted < self._batch_size:
                break
        return total


async def _stopped_within(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        return False
    return True

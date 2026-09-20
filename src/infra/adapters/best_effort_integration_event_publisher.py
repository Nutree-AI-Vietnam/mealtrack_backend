"""Wrap Queue publication so transport failures cannot fail a committed write."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)

logger = logging.getLogger(__name__)

_DRAIN_TIMEOUT_SECONDS = 5.0


class BestEffortIntegrationEventPublisher:
    """Publish after commit on a background task; log Queue failures and continue."""

    def __init__(self, inner: IntegrationEventPublisherPort) -> None:
        self._inner = inner
        self._pending: set[asyncio.Task[None]] = set()

    async def publish(self, payload: dict[str, Any]) -> None:
        task = asyncio.create_task(
            self._publish_safe(payload),
            name="integration-event-publish",
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self, timeout: float = _DRAIN_TIMEOUT_SECONDS) -> None:
        """Wait for in-flight Queue publishes during process shutdown."""
        pending = [task for task in self._pending if not task.done()]
        if not pending:
            return
        _done, still_running = await asyncio.wait(pending, timeout=timeout)
        for task in still_running:
            task.cancel()
        if still_running:
            await asyncio.gather(*still_running, return_exceptions=True)

    async def _publish_safe(self, payload: dict[str, Any]) -> None:
        try:
            await self._inner.publish(payload)
        except Exception:
            event_type = (
                payload.get("event_type") if isinstance(payload, dict) else None
            )
            aggregate_id = (
                payload.get("aggregate_id") if isinstance(payload, dict) else None
            )
            logger.warning(
                "integration event publish failed; continuing without queue",
                extra={"event_type": event_type, "aggregate_id": aggregate_id},
                exc_info=True,
            )

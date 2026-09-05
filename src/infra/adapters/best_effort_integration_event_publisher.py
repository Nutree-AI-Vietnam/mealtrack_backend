"""Wrap Queue publication so transport failures cannot fail a committed write."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)

logger = logging.getLogger(__name__)


class BestEffortIntegrationEventPublisher:
    """Publish after commit; log Queue failures and continue."""

    def __init__(self, inner: IntegrationEventPublisherPort) -> None:
        self._inner = inner

    async def publish(self, payload: dict[str, Any]) -> None:
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

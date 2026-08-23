"""Port for publishing integration-event payloads outside the application layer."""

from __future__ import annotations

from typing import Any, Protocol


class IntegrationEventPublisherPort(Protocol):
    """Publish one JSON-compatible integration event payload."""

    async def publish(self, payload: dict[str, Any]) -> None:
        """Publish a payload to the configured integration-event transport."""

"""Port for publishing integration-event payloads outside the application layer."""

from __future__ import annotations

from typing import Any, Protocol


class IntegrationEventPublisherPort(Protocol):
    """Publish one JSON-compatible integration event payload."""

    async def publish(self, payload: dict[str, Any]) -> None:
        """Publish a payload to the configured integration-event transport."""


class IntegrationEventPublisherRequiredError(RuntimeError):
    """Raised when a Queue-backed event path has no configured publisher."""


def require_event_publisher(
    publisher: IntegrationEventPublisherPort | None,
) -> IntegrationEventPublisherPort:
    """Return the configured publisher or fail instead of dropping an event."""
    if publisher is None:
        raise IntegrationEventPublisherRequiredError(
            "Integration event publisher is required"
        )
    return publisher

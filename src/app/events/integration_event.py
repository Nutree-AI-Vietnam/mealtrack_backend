"""Compatibility imports for the shared integration-event envelope."""

from src.domain.events.integration_event import (
    INTEGRATION_EVENT_VERSION,
    MAX_INTEGRATION_EVENT_BYTES,
    IntegrationEvent,
)

__all__ = [
    "INTEGRATION_EVENT_VERSION",
    "MAX_INTEGRATION_EVENT_BYTES",
    "IntegrationEvent",
]

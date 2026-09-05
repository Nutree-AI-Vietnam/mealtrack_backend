"""Integration event emitted after a hydration entry is deleted."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class HydrationDeletedEvent(IntegrationEvent):
    """Published after a hydration entry is deleted."""

    event_type: Literal["hydration.deleted.v1"] = "hydration.deleted.v1"
    aggregate_type: Literal["hydration"] = "hydration"


# Alias for backward-compatibility or alternate naming
HydrateDeletedEvent = HydrationDeletedEvent

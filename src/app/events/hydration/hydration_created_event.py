"""Integration event emitted after a hydration entry is persisted."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class HydrationCreatedEvent(IntegrationEvent):
    """Published after a hydration is persisted."""

    event_type: Literal["hydration.created.v1"] = "hydration.created.v1"
    aggregate_type: Literal["hydration"] = "hydration"

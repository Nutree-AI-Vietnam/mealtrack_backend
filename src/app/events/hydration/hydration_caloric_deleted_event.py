"""Integration event emitted after a caloric drink hydration entry is deleted."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class HydrationCaloricDeletedEvent(IntegrationEvent):
    """Published after a caloric drink hydration entry is deleted."""

    event_type: Literal["hydration.caloric_deleted.v1"] = "hydration.caloric_deleted.v1"
    aggregate_type: Literal["hydration"] = "hydration"

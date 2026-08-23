"""Integration event emitted after a caloric drink hydration entry is persisted."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class HydrationCaloricCreatedEvent(IntegrationEvent):
    """Published after a caloric drink hydration entry is persisted."""

    event_type: Literal["hydration.caloric_created.v1"] = "hydration.caloric_created.v1"
    aggregate_type: Literal["hydration"] = "hydration"

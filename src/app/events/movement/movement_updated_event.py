"""Integration event emitted after a movement entry is updated."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class MovementUpdatedEvent(IntegrationEvent):
    """Published after a movement entry is updated."""

    event_type: Literal["movement.updated.v1"] = "movement.updated.v1"
    aggregate_type: Literal["movement"] = "movement"

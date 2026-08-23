"""Integration event emitted after a movement entry is deleted."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class MovementDeletedEvent(IntegrationEvent):
    """Published after a movement entry is deleted."""

    event_type: Literal["movement.deleted.v1"] = "movement.deleted.v1"
    aggregate_type: Literal["movement"] = "movement"

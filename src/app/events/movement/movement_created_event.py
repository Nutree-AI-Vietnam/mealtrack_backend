"""Integration event emitted after a movement entry is logged."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class MovementCreatedEvent(IntegrationEvent):
    """Published after a movement entry is persisted."""

    event_type: Literal["movement.created.v1"] = "movement.created.v1"
    aggregate_type: Literal["movement"] = "movement"

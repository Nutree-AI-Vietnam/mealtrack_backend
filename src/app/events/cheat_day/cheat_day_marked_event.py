"""Integration event emitted after a cheat day is marked."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class CheatDayMarkedEvent(IntegrationEvent):
    """Published after a cheat day is persisted."""

    event_type: Literal["cheat_day.marked.v1"] = "cheat_day.marked.v1"
    aggregate_type: Literal["cheat_day"] = "cheat_day"

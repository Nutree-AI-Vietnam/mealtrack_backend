"""Integration event emitted after a cheat day is unmarked."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class CheatDayUnmarkedEvent(IntegrationEvent):
    """Published after a cheat day is deleted."""

    event_type: Literal["cheat_day.unmarked.v1"] = "cheat_day.unmarked.v1"
    aggregate_type: Literal["cheat_day"] = "cheat_day"

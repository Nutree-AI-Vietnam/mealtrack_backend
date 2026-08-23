"""Integration event emitted after user custom macros are updated."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class UserCustomMacrosUpdatedEvent(IntegrationEvent):
    """Published after user custom macros are updated."""

    event_type: Literal["user.custom_macros_updated.v1"] = "user.custom_macros_updated.v1"
    aggregate_type: Literal["user"] = "user"

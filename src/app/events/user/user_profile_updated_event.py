"""Integration event emitted after a user profile is updated."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class UserProfileUpdatedEvent(IntegrationEvent):
    """Published after a user profile or metrics is updated."""

    event_type: Literal["user.profile_updated.v1"] = "user.profile_updated.v1"
    aggregate_type: Literal["user"] = "user"

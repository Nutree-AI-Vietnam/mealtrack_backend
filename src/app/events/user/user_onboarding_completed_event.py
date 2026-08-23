"""Integration event emitted after a user completes onboarding."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class UserOnboardingCompletedEvent(IntegrationEvent):
    """Published after a user completes onboarding."""

    event_type: Literal["user.onboarding_completed.v1"] = "user.onboarding_completed.v1"
    aggregate_type: Literal["user"] = "user"

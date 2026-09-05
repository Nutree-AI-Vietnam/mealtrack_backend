"""Integration event emitted after a meal suggestion is saved."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class SavedSuggestionCreatedEvent(IntegrationEvent):
    """Published after a meal suggestion bookmark is saved."""

    event_type: Literal["saved_suggestion.created.v1"] = "saved_suggestion.created.v1"
    aggregate_type: Literal["saved_suggestion"] = "saved_suggestion"

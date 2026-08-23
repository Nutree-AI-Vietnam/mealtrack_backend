"""Integration event emitted after a meal suggestion bookmark is deleted."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class SavedSuggestionDeletedEvent(IntegrationEvent):
    """Published after a meal suggestion bookmark is deleted."""

    event_type: Literal["saved_suggestion.deleted.v1"] = "saved_suggestion.deleted.v1"
    aggregate_type: Literal["saved_suggestion"] = "saved_suggestion"

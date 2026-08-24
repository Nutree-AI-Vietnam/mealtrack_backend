"""Meal domain integration events emitted to external consumers."""

from __future__ import annotations

from typing import Literal

from src.app.events.integration_event import IntegrationEvent


class MealCreatedEvent(IntegrationEvent):
    """Published after a meal is created."""

    event_type: Literal["meal.created.v1"] = "meal.created.v1"
    aggregate_type: Literal["meal"] = "meal"


class MealUpdatedEvent(IntegrationEvent):
    """Published after a meal is edited or updated."""

    event_type: Literal["meal.updated.v1"] = "meal.updated.v1"
    aggregate_type: Literal["meal"] = "meal"


class MealDeletedEvent(IntegrationEvent):
    """Published after a meal is deleted."""

    event_type: Literal["meal.deleted.v1"] = "meal.deleted.v1"
    aggregate_type: Literal["meal"] = "meal"

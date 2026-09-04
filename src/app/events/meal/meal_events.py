"""Meal domain integration events emitted to external consumers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Literal

from src.app.events.integration_event import IntegrationEvent
from src.app.events.meal.meal_insight_snapshot import (
    MealInsightSnapshot,
    compact_insight_user_context,
    insight_language_code,
    meal_insight_occurred_at,
)
from src.domain.model.meal import Meal
from src.domain.ports.integration_event_publisher_port import require_event_publisher

logger = logging.getLogger(__name__)

LocalInsightHook = Callable[[str, dict[str, Any], datetime], None]
_local_insight_hook: LocalInsightHook | None = None


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


def register_local_insight_hook(hook: LocalInsightHook | None) -> None:
    """Register the optional local-Redis insight writer used in development."""
    global _local_insight_hook
    _local_insight_hook = hook


async def _insight_user_context(
    event_bus: Any | None, user_id: str
) -> dict[str, Any] | None:
    if event_bus is None or not user_id:
        return None
    try:
        from src.app.queries.user import GetUserProfileQuery

        result = await event_bus.send(GetUserProfileQuery(user_id=user_id))
    except Exception:
        logger.info("meal insight skipped profile context user_id=%s", user_id)
        return None
    return compact_insight_user_context(result)


def _insight_payload(
    meal: Meal,
    *,
    language: str,
    user_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if getattr(meal, "nutrition", None) is None:
        return None
    try:
        return MealInsightSnapshot.from_meal(
            meal,
            language=language,
            user_context=user_context,
        ).model_dump(mode="json", exclude_none=True)
    except ValueError:
        return None


async def publish_meal_event(
    publisher: Any,
    meal: Meal,
    *,
    event_type: Literal["created", "updated"],
    environment: str,
    meal_date: date | datetime,
    user_id: str | None = None,
    language: str = "en",
    event_bus: Any | None = None,
    old_meal_date: date | datetime | None = None,
    source: str = "meal_write",
) -> bool:
    """Publish one committed meal integration event to the external Queue consumer."""
    publisher = require_event_publisher(publisher)
    logger.debug("publishing meal event source=%s", source)
    resolved_user_id = str(user_id or getattr(meal, "user_id", "") or "")
    user_context = await _insight_user_context(event_bus, resolved_user_id)
    preferred_language = None
    if isinstance(user_context, dict):
        preferred_language = user_context.get("language_code")
    resolved_language = insight_language_code(
        language or preferred_language or getattr(meal, "language", None)
    )
    data: dict[str, Any] = {
        "user_id": resolved_user_id,
        "meal_id": str(meal.meal_id),
        "meal_date": meal_date.isoformat(),
        "language": resolved_language,
    }
    if old_meal_date is not None and old_meal_date != meal_date:
        data["old_meal_date"] = old_meal_date.isoformat()

    insight = _insight_payload(
        meal,
        language=resolved_language,
        user_context=user_context,
    )
    if insight is not None:
        data["insight"] = insight

    event_class = MealCreatedEvent if event_type == "created" else MealUpdatedEvent
    occurred_at = meal_insight_occurred_at(meal)
    event = event_class(
        environment=environment,
        aggregate_id=str(meal.meal_id),
        occurred_at=occurred_at,
        data=data,
    )
    await publisher.publish(event.to_payload())
    if insight is not None and _local_insight_hook is not None:
        _local_insight_hook(str(meal.meal_id), insight, occurred_at)
    return True

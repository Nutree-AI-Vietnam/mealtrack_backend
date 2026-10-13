"""Facts the empty coach screen may show. Numbers stay server-owned."""

from __future__ import annotations

from typing import Any

from src.domain.model.chat import ChatUserContext

_SLOTS = frozenset({"breakfast", "lunch", "snack", "dinner"})
_MAX_LABEL = 40


def coach_welcome(context: ChatUserContext) -> dict[str, Any]:
    """Bounded greeting facts. Missing values stay null so the client stays generic."""
    slot = context.suggested_meal_slot
    if slot not in _SLOTS:
        slot = None
    return {
        "preferred_name": _label(context.preferred_name),
        "remaining_calories": _round_number(context.remaining_calories),
        "remaining_protein_g": _round_number(context.remaining_protein_g),
        "remaining_carbs_g": _round_number(context.remaining_carbs_g),
        "suggested_meal_slot": slot,
        "latest_meal_name": _latest_meal_name(context),
    }


def _latest_meal_name(context: ChatUserContext) -> str | None:
    meals = context.recent_meals
    if context.local_date:
        today = tuple(meal for meal in meals if meal.local_date == context.local_date)
        if today:
            meals = today
    for meal in reversed(meals):
        name = _label(meal.dish_name)
        if name:
            return name
    return None


def _label(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > _MAX_LABEL:
        return None
    return cleaned


def _round_number(value: float | None) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None

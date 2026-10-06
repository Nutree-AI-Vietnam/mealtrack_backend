"""Identify weekly plans written by the two-meal server after breakfast shipped."""

from __future__ import annotations

from src.domain.model.weekly_meal_planner.weekly_meal_plan import WEEKLY_DAYS


def is_late_legacy_two_slot_plan(
    *,
    algorithm_version: str,
    status: str,
    coordinates: set[tuple[int, int]],
) -> bool:
    """Return whether this draft is exactly seven lunch/dinner pairs.

    Slot 0 holds lunch and slot 1 holds dinner. Breakfast is absent. Any
    other shape, including a modern partial week, is left untouched.
    """
    if algorithm_version != "v1" or status != "draft":
        return False
    if len(coordinates) != WEEKLY_DAYS * 2:
        return False
    return coordinates == {(day, slot) for day in range(WEEKLY_DAYS) for slot in (0, 1)}

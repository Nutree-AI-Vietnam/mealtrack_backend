"""Coordinate and revision guards shared by weekly plan writes."""

from __future__ import annotations

from src.domain.model.weekly_meal_planner.weekly_meal_plan import ensure_slot_coordinate

__all__ = ["ensure_base_revision", "ensure_slot_coordinate"]


def ensure_base_revision(base_revision: int, current_revision: int) -> None:
    if base_revision != current_revision:
        raise ValueError("stale base_revision")

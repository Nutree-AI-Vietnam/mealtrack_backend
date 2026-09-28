"""Provider port for structured weekly-plan adjustment proposals."""

from __future__ import annotations

from typing import Protocol

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
)


class WeeklyMealPlanAdjustmentProvider(Protocol):
    """Return an untrusted, structured proposal for an existing plan."""

    async def propose(
        self,
        *,
        prompt: str,
        plan: WeeklyMealPlan,
        meals: tuple[CatalogMeal, ...],
        preferences: WeeklyMealPlanPreferences | None = None,
        profile_dietary_preferences: tuple[str, ...] = (),
        target_day_index: int | None = None,
        target_slot_index: int | None = None,
    ) -> object:
        """Return provider output for deterministic application validation."""

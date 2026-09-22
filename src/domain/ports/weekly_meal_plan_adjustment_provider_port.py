"""Provider port for structured weekly-plan adjustment proposals."""

from __future__ import annotations

from typing import Protocol

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import WeeklyMealPlan


class WeeklyMealPlanAdjustmentProvider(Protocol):
    """Return an untrusted, structured proposal for an existing plan."""

    async def propose(
        self,
        *,
        prompt: str,
        plan: WeeklyMealPlan,
        meals: tuple[CatalogMeal, ...],
    ) -> object:
        """Return provider output for deterministic application validation."""

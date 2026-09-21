"""Pure structured proposal values for weekly-plan adjustments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeeklyMealPlanSlotAdjustment:
    day_index: int
    slot_index: int
    action: str
    new_recipe_id: str | None


@dataclass(frozen=True)
class WeeklyMealPlanAdjustmentProposal:
    explanation: str
    slot_changes: tuple[WeeklyMealPlanSlotAdjustment, ...]

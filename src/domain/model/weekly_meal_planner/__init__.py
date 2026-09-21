"""Domain models for weekly meal planning."""

from .ai_proposal import (
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanSlotAdjustment,
)
from .weekly_meal_plan import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)

__all__ = [
    "WeeklyMealPlanAdjustmentProposal",
    "WeeklyMealPlanSlotAdjustment",
    "WeeklyMealPlan",
    "WeeklyMealPlanPreferences",
    "WeeklyMealPlanSlot",
    "WeeklyMealPlanStatus",
]

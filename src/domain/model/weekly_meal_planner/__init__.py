"""Domain models for weekly meal planning."""

from .ai_proposal import (
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanSlotAdjustment,
)
from .weekly_meal_plan import (
    SLOT_MEAL_TYPES,
    WEEKLY_DAYS,
    WEEKLY_PLAN_SLOT_COUNT,
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
    meal_type_for_slot,
)

__all__ = [
    "SLOT_MEAL_TYPES",
    "WEEKLY_DAYS",
    "WEEKLY_PLAN_SLOT_COUNT",
    "WEEKLY_SLOTS_PER_DAY",
    "WeeklyMealPlanAdjustmentProposal",
    "WeeklyMealPlanSlotAdjustment",
    "WeeklyMealPlan",
    "WeeklyMealPlanPreferences",
    "WeeklyMealPlanSlot",
    "WeeklyMealPlanStatus",
    "meal_type_for_slot",
]

"""Domain models for weekly meal planning."""

from .ai_proposal import (
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanSlotAdjustment,
)
from .saved_recipe import (
    SavedRecipeIngredient,
    SavedRecipeSnapshot,
    SavedRecipeStep,
)
from .weekly_meal_plan import (
    SLOT_LOG_SOURCE_DIARY,
    SLOT_LOG_SOURCE_PLAN,
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
    "SLOT_LOG_SOURCE_DIARY",
    "SLOT_LOG_SOURCE_PLAN",
    "SLOT_MEAL_TYPES",
    "WEEKLY_DAYS",
    "WEEKLY_PLAN_SLOT_COUNT",
    "WEEKLY_SLOTS_PER_DAY",
    "SavedRecipeIngredient",
    "SavedRecipeSnapshot",
    "SavedRecipeStep",
    "WeeklyMealPlanAdjustmentProposal",
    "WeeklyMealPlanSlotAdjustment",
    "WeeklyMealPlan",
    "WeeklyMealPlanPreferences",
    "WeeklyMealPlanSlot",
    "WeeklyMealPlanStatus",
    "meal_type_for_slot",
]

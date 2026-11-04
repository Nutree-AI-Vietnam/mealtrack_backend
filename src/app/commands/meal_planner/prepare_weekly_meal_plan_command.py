"""Open a week for a model to write into."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.domain.model.weekly_meal_planner.weekly_meal_plan import (
    WeeklyMealPlanPreferences,
)


@dataclass(frozen=True)
class PrepareWeeklyMealPlanCommand:
    """Create the week's plan with no dishes yet, waiting in 'preparing'.

    The dishes arrive from a background job. The row exists first so the
    planner has something to show, and so the job has an id to write into
    that survives a retry.
    """

    user_id: str
    week_start_date: date
    timezone: str
    preferences: WeeklyMealPlanPreferences
    daily_calories: int

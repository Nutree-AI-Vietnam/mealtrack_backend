from dataclasses import dataclass
from datetime import date

from src.app.events.base import Command
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences


@dataclass
class GenerateWeeklyMealPlanCommand(Command):
    user_id: str
    week_start_date: date
    timezone: str
    preferences: WeeklyMealPlanPreferences
    idempotency_key: str
    daily_calories: int
    # The day's other targets, when the weekly budget knows them. Without
    # them the plan fits calories alone, as it did before composing.
    daily_protein: float | None = None
    daily_carbs: float | None = None
    daily_fat: float | None = None

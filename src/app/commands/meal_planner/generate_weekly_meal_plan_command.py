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

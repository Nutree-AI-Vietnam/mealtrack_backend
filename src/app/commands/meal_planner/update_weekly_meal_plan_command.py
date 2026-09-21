from dataclasses import dataclass

from src.app.events.base import Command
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences


@dataclass
class UpdateWeeklyMealPlanCommand(Command):
    user_id: str
    plan_id: str
    idempotency_key: str
    people: int | None = None
    preferences: WeeklyMealPlanPreferences | None = None
    status: str | None = None
    slots: dict[tuple[int, int], str | None] | None = None
    expected_revision: int | None = None

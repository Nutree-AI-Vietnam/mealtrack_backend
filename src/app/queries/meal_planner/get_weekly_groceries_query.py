from dataclasses import dataclass

from src.app.events.base import Query
from src.domain.model.weekly_meal_planner import WeeklyMealPlan


@dataclass
class GetWeeklyGroceriesQuery(Query):
    user_id: str
    plan_id: str

    plan: WeeklyMealPlan | None = None

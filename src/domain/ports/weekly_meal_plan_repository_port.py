"""Port for owner-scoped weekly meal plan persistence."""

from abc import ABC, abstractmethod
from datetime import date

from src.domain.model.weekly_meal_planner import WeeklyMealPlan


class WeeklyMealPlanRepositoryPort(ABC):
    """Persistence contract kept independent from SQLAlchemy."""

    @abstractmethod
    async def get_current(
        self, *, user_id: str, week_start_date: date
    ) -> WeeklyMealPlan | None:
        raise NotImplementedError

    @abstractmethod
    async def get_by_id(self, *, user_id: str, plan_id: str) -> WeeklyMealPlan | None:
        raise NotImplementedError

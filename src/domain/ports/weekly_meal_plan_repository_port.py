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

    async def list_plan_recipe_outcomes(
        self, *, user_id: str, since: date, limit: int = 400
    ) -> list[tuple[str, str]]:
        """What became of each planned dish: (recipe_id, "logged"|"skipped").

        One entry per slot, so repeats carry their count. Used to learn which
        dishes to plan again and which to stop planning. An implementation
        that cannot answer returns nothing and the planner keeps its old
        behaviour.
        """
        return []

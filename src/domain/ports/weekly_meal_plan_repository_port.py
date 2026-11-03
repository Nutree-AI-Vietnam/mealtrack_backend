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

    async def list_skipped_recipe_ids(
        self, *, user_id: str, since: date, limit: int = 200
    ) -> list[str]:
        """Catalog recipes the user skipped, one entry per skip.

        Used to learn which dishes to stop planning. An implementation that
        cannot answer returns nothing and the planner keeps its old
        behaviour.
        """
        return []

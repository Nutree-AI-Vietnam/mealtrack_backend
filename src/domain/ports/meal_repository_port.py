from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

from src.domain.model.meal import Meal, MealStatus
from src.domain.services.meal_recommendation.ingredient_affinity_service import (
    IngredientHistoryBucket,
)


class MealRepositoryPort(ABC):
    """Port interface for meal persistence operations."""

    @abstractmethod
    async def save(self, meal: Meal) -> Meal:
        """
        Persists a meal entity.

        Args:
            meal: The meal to be saved

        Returns:
            The saved meal with any generated IDs
        """
        pass

    async def insert(self, meal: Meal) -> Meal:
        """Insert a new meal row. Must not UPDATE an existing primary key."""
        return await self.save(meal)

    @abstractmethod
    async def find_by_id(self, meal_id: str, projection: Any = None) -> Meal | None:
        """
        Finds a meal by its ID.

        Args:
            meal_id: The ID of the meal to find
            projection: Optional projection hint (e.g. MealProjection)
                        to control which relationships are loaded.

        Returns:
            The meal if found, None otherwise
        """
        pass

    @abstractmethod
    async def find_by_status(self, status: MealStatus, limit: int = 10) -> list[Meal]:
        """
        Finds meals by status.

        Args:
            status: The status to filter by
            limit: Maximum number of results

        Returns:
            List of meals with the specified status
        """
        pass

    @abstractmethod
    async def find_all_paginated(self, offset: int = 0, limit: int = 20) -> list[Meal]:
        """
        Retrieves all meals with pagination.

        Args:
            offset: Pagination offset
            limit: Maximum number of results

        Returns:
            Paginated list of meals
        """
        pass

    @abstractmethod
    async def count(self) -> int:
        """
        Counts the total number of meals.

        Returns:
            Total count
        """
        pass

    @abstractmethod
    async def find_by_date(
        self,
        date_obj: date,
        user_id: str = None,
        limit: int = 50,
        user_timezone: str | None = None,
        projection: Any = None,
    ) -> list[Meal]:
        """
        Finds meals created on a specific date, optionally filtered by user.

        Args:
            date_obj: The date to filter by (date object)
            user_id: Optional user ID to filter meals by specific user
            limit: Maximum number of results
            projection: Optional projection hint (e.g. MealProjection)
                        to control which relationships are loaded.

        Returns:
            List of meals created on the specified date
        """
        pass

    async def find_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        limit: int = 500,
        user_timezone: str | None = None,
        projection: Any = None,
    ) -> list[Meal]:
        """Find meals created within a local date range, inclusive."""
        return []

    async def find_recent_food_meals(
        self,
        user_id: str,
        start_dt: datetime,
        end_dt: datetime,
        limit: int = 500,
        projection: Any = None,
    ) -> list[Meal]:
        """Return READY food meals (no hydration) in [start_dt, end_dt), newest first."""
        return []

    async def aggregate_linked_ingredient_history(
        self,
        *,
        user_id: str,
        start_date: date,
        end_date: date,
        reference_date: date,
        user_timezone: str | None = None,
    ) -> list[IngredientHistoryBucket]:
        """Return linked canonical ingredient usage buckets for recommendations."""
        return []

    @abstractmethod
    async def delete(self, meal_id: str) -> None:
        """Delete a meal by ID with data preservation."""
        pass

    async def get_daily_meal_counts(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> dict[date, int]:
        """Return {date: meal_count} for each day in range with at least 1 meal."""
        return {}

    async def fetch_journey_progress_meals(
        self,
        user_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[dict]:
        """Return meal action rows in a strict UTC range for journey progress."""
        return []

    async def list_logged_catalog_meal_ids(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> tuple[tuple[str, datetime], ...]:
        """Unique catalog_meal_ids this user logged, most recent first."""
        return ()

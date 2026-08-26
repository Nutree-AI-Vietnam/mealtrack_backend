"""Fake meal repository for testing."""

from datetime import date
from typing import Any, List, Optional

from sqlalchemy.exc import IntegrityError

from src.domain.model.meal import Meal, MealStatus
from src.domain.ports.meal_repository_port import MealRepositoryPort


class FakeMealRepository(MealRepositoryPort):
    """In-memory implementation of MealRepositoryPort for testing."""

    def __init__(self):
        self._meals: dict[str, Meal] = {}

    async def save(self, meal: Meal) -> Meal:
        """Save a meal to in-memory storage."""
        self._meals[meal.meal_id] = meal
        return meal

    async def insert(self, meal: Meal) -> Meal:
        if meal.meal_id in self._meals:
            raise IntegrityError("insert", {}, Exception("duplicate meal_id"))
        return await self.save(meal)

    async def find_by_id(self, meal_id: str, projection: Any = None) -> Optional[Meal]:
        """Find a meal by ID."""
        return self._meals.get(meal_id)

    async def find_by_status(self, status: MealStatus, limit: int = 10) -> List[Meal]:
        """Find meals by status."""
        return [meal for meal in self._meals.values() if meal.status == status][:limit]

    async def find_all_paginated(self, offset: int = 0, limit: int = 20) -> List[Meal]:
        """Retrieve all meals with pagination."""
        meals = list(self._meals.values())
        return meals[offset : offset + limit]

    async def count(self) -> int:
        """Count total number of meals."""
        return len(self._meals)

    async def find_by_date(
        self,
        date_obj: date,
        user_id: str = None,
        limit: int = 50,
        user_timezone: Optional[str] = None,
        projection: Any = None,
    ) -> List[Meal]:
        """Find meals created on a specific date."""
        meals = [
            meal for meal in self._meals.values() if meal.created_at.date() == date_obj
        ]

        if user_id:
            meals = [meal for meal in meals if meal.user_id == user_id]

        return meals[:limit]

    async def find_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        limit: int = 500,
        user_timezone: Optional[str] = None,
        projection: Any = None,
    ) -> List[Meal]:
        """Find meals created within a date range."""
        meals = [
            meal
            for meal in self._meals.values()
            if start_date <= meal.created_at.date() <= end_date
            and meal.user_id == user_id
        ]
        return meals[:limit]

    async def delete(self, meal_id: str) -> bool:
        """Delete a meal by ID."""
        if meal_id in self._meals:
            del self._meals[meal_id]
            return True
        return False

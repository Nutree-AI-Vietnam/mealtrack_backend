"""Port interface for durable favorite meal membership."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from src.domain.model.meal import Meal


class FavoriteMealRepositoryPort(ABC):
    """Port for persisting and reading user favorite meals."""

    @abstractmethod
    async def favorite(
        self,
        user_id: str,
        meal_id: str,
        favorited_at: datetime | None = None,
    ) -> bool:
        """Add a meal to the user's favorites idempotently.

        Returns:
            True if inserted, False if already favorited.
        """
        pass

    @abstractmethod
    async def unfavorite(self, user_id: str, meal_id: str) -> bool:
        """Remove a meal from the user's favorites idempotently.

        Returns:
            True if a row was removed, False if no favorite existed.
        """
        pass

    @abstractmethod
    async def is_favorite(self, user_id: str, meal_id: str) -> bool:
        """Check if a specific meal is currently favorited by the user."""
        pass

    @abstractmethod
    async def filter_favorited_meal_ids(
        self,
        user_id: str,
        meal_ids: Sequence[str],
    ) -> set[str]:
        """Return the subset of meal_ids that are favorited by the user."""
        pass

    @abstractmethod
    async def list_favorite_meals(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[tuple[Meal, datetime]]:
        """Return user's favorited meals ordered newest-favorited first.

        Returns:
            List of (Meal, favorited_at) tuples, including active and inactive meals.
        """
        pass

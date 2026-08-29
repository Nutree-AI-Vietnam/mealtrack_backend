"""Abstract cache port for recent and favorite meal list projections."""

from abc import ABC, abstractmethod
from typing import Any


class MealListCachePort(ABC):
    """Port for caching recent and favorite meal list projections."""

    @abstractmethod
    async def get_revision(self, user_id: str) -> int:
        """Get the current list revision for a user."""
        ...

    @abstractmethod
    async def increment_revision(self, user_id: str) -> int:
        """Increment the user's meal list revision to invalidate cached lists."""
        ...

    @abstractmethod
    async def get_recent_meals(
        self, user_id: str, revision: int, timezone_str: str, language: str
    ) -> dict[str, Any] | None:
        """Get cached recent meals payload."""
        ...

    @abstractmethod
    async def set_recent_meals(
        self,
        user_id: str,
        revision: int,
        timezone_str: str,
        language: str,
        data: dict[str, Any],
        ttl: int = 300,
    ) -> None:
        """Cache recent meals payload."""
        ...

    @abstractmethod
    async def get_favorite_meals(
        self, user_id: str, revision: int, language: str
    ) -> dict[str, Any] | None:
        """Get cached favorite meals payload."""
        ...

    @abstractmethod
    async def set_favorite_meals(
        self,
        user_id: str,
        revision: int,
        language: str,
        data: dict[str, Any],
        ttl: int = 300,
    ) -> None:
        """Cache favorite meals payload."""
        ...

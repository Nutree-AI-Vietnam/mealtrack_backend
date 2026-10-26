"""Port interface for stored weekly recaps."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from src.domain.model.weekly_recap import WeeklyRecap


class WeeklyRecapRepositoryPort(ABC):
    """Port for reading and writing one recap per user per week."""

    @abstractmethod
    async def get(self, user_id: str, week_start: date) -> WeeklyRecap | None:
        """Return the user's recap for the week starting ``week_start``."""
        pass

    @abstractmethod
    async def insert_if_absent(self, recap: WeeklyRecap) -> bool:
        """Store the recap unless the user already has one for that week.

        Returns:
            True if inserted, False if a recap for the week already existed.
        """
        pass

    @abstractmethod
    async def delete_for_user(self, user_id: str) -> int:
        """Delete every recap of the user, e.g. when the account is deleted.

        Returns:
            How many recaps were deleted.
        """
        pass

    @abstractmethod
    async def count_cases_before(self, user_id: str, before: date) -> dict[str, int]:
        """Count the user's recaps per story for weeks starting before ``before``.

        Returns:
            Mapping of case id (e.g. ``"perfect"``) to number of weeks.
        """
        pass

"""
This module defines the abstract port for the user repository, ensuring a clean
separation between the domain and infrastructure layers.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from src.domain.model.user import UserDomainModel, UserProfileDomainModel


class UserRepositoryPort(ABC):
    """
    Interface for user repository operations.
    This port works exclusively with domain models.
    """

    @abstractmethod
    async def save(self, user: UserDomainModel) -> UserDomainModel:
        """Save or update a user."""
        pass

    @abstractmethod
    async def find_by_id(self, user_id: UUID) -> UserDomainModel | None:
        """Find a user by ID."""
        pass

    @abstractmethod
    async def find_by_firebase_uid(self, firebase_uid: str) -> UserDomainModel | None:
        """Find a user by Firebase UID (active users only)."""
        pass

    @abstractmethod
    async def find_deleted_by_firebase_uid(
        self, firebase_uid: str
    ) -> UserDomainModel | None:
        """Find a deleted user by Firebase UID (inactive users only)."""
        pass

    @abstractmethod
    async def find_by_email(self, email: str) -> UserDomainModel | None:
        """Find a user by email."""
        pass

    @abstractmethod
    async def find_all(
        self, limit: int = 100, offset: int = 0
    ) -> list[UserDomainModel]:
        """Find all users with pagination."""
        pass

    @abstractmethod
    async def delete(self, user_id: UUID) -> bool:
        """Delete a user by ID."""
        pass

    @abstractmethod
    async def get_profile(self, user_id: UUID) -> UserProfileDomainModel | None:
        """Get user profile by user ID."""
        pass

    @abstractmethod
    async def update_profile(
        self, profile: UserProfileDomainModel
    ) -> UserProfileDomainModel:
        """Update user profile."""
        pass

    @abstractmethod
    async def update_user_timezone(self, user_id: UUID, timezone: str) -> None:
        """Update user's timezone in the database."""
        pass

    @abstractmethod
    async def get_user_timezone(self, user_id: UUID) -> str | None:
        """Get user's timezone from database."""
        pass

    @abstractmethod
    async def update_user_language(self, user_id: UUID, language_code: str) -> None:
        """Update user's preferred language."""
        pass

    @abstractmethod
    async def get_weekly_auto_adjust(self, user_id: UUID) -> bool:
        """Return whether leftover-split daily adjustment is enabled."""
        pass

    @abstractmethod
    async def update_user_weekly_auto_adjust(
        self, user_id: UUID, enabled: bool
    ) -> None:
        """Persist leftover-split daily adjustment preference."""
        pass

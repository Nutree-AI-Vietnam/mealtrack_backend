from typing import List, Optional
from uuid import UUID

from src.domain.model.user import UserDomainModel, UserProfileDomainModel
from src.domain.ports.user_repository_port import UserRepositoryPort


class FakeUserRepository(UserRepositoryPort):
    def __init__(self):
        self.users = {}  # id -> user
        self.profiles = {}  # user_id -> profile
        self.weekly_auto_adjust = {}  # user_id str -> bool

    async def save(self, user: UserDomainModel) -> UserDomainModel:
        self.users[user.id] = user
        return user

    async def find_by_id(self, user_id: UUID) -> Optional[UserDomainModel]:
        return self.users.get(user_id)

    async def find_by_firebase_uid(
        self, firebase_uid: str
    ) -> Optional[UserDomainModel]:
        """Find active user by Firebase UID."""
        for user in self.users.values():
            if user.firebase_uid == firebase_uid and user.is_active:
                return user
        return None

    async def find_deleted_by_firebase_uid(
        self, firebase_uid: str
    ) -> Optional[UserDomainModel]:
        """Find deleted (inactive) user by Firebase UID."""
        for user in self.users.values():
            if user.firebase_uid == firebase_uid and not user.is_active:
                return user
        return None

    async def find_by_email(self, email: str) -> Optional[UserDomainModel]:
        for user in self.users.values():
            if user.email == email and user.is_active:
                return user
        return None

    async def find_all(
        self, limit: int = 100, offset: int = 0
    ) -> List[UserDomainModel]:
        return list(self.users.values())[offset : offset + limit]

    async def delete(self, user_id: UUID) -> bool:
        """Soft delete user by marking as inactive (matches real repository behavior)."""
        if user_id in self.users:
            self.users[user_id].is_active = False
            return True
        return False

    async def get_profile(self, user_id: UUID) -> Optional[UserProfileDomainModel]:
        return self.profiles.get(user_id)

    async def update_profile(
        self, profile: UserProfileDomainModel
    ) -> UserProfileDomainModel:
        self.profiles[profile.user_id] = profile
        return profile

    async def update_user_timezone(self, firebase_uid: str, timezone: str) -> None:
        """Update user's timezone (no-op for fake repository)."""
        pass

    async def get_user_timezone(self, firebase_uid: str) -> Optional[str]:
        """Get user's timezone from database (returns None for fake repository)."""
        return None

    async def update_user_language(self, user_id: UUID, language_code: str) -> None:
        """Update user's language preference."""
        if user_id in self.users:
            self.users[user_id].language_code = language_code

    async def get_weekly_auto_adjust(self, user_id: UUID) -> bool:
        return self.weekly_auto_adjust.get(str(user_id), True)

    async def update_user_weekly_auto_adjust(
        self, user_id: UUID, enabled: bool
    ) -> None:
        self.weekly_auto_adjust[str(user_id)] = enabled

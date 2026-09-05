"""Async user repository backed by asyncpg + AsyncSession."""

import logging
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.exceptions.firebase_identity_exceptions import (
    FirebaseIdentityConflictError,
)
from src.domain.model.user import UserDomainModel, UserProfileDomainModel
from src.domain.ports.user_repository_port import UserRepositoryPort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.models.user.profile_preference import UserProfilePreference
from src.infra.database.models.user.user import User
from src.infra.mappers.user_mapper import (
    UserMapper,
    UserProfileMapper,
    build_profile_preference_entries,
)

logger = logging.getLogger(__name__)

_USER_LOADS = (
    selectinload(User.profiles).selectinload(UserProfile.preference_entries),
    selectinload(User.subscriptions),
)

_USER_SAVE_COLUMNS = (
    "firebase_uid",
    "email",
    "username",
    "password_hash",
    "provider",
    "is_active",
    "onboarding_completed",
    "last_accessed",
    "timezone",
    "first_name",
    "last_name",
    "phone_number",
    "display_name",
    "photo_url",
    "deleted_at",
)


def _copy_user_save_columns(entity: User, updated: User) -> None:
    for col_name in _USER_SAVE_COLUMNS:
        new_val = getattr(updated, col_name)
        if getattr(entity, col_name) != new_val:
            setattr(entity, col_name, new_val)


def _sync_profile_preference_entries(
    entity: UserProfile,
    profile_domain: UserProfileDomainModel,
) -> None:
    desired_entries = build_profile_preference_entries(profile_domain)
    existing_by_key: dict[tuple[str, str], UserProfilePreference] = {
        (entry.preference_type, entry.value): entry
        for entry in entity.preference_entries
    }

    next_entries: list[UserProfilePreference] = []
    for desired in desired_entries:
        key = (desired.preference_type, desired.value)
        existing = existing_by_key.pop(key, None)
        if existing:
            existing.position = desired.position
            next_entries.append(existing)
            continue

        desired.profile_id = str(entity.id)
        next_entries.append(desired)

    entity.preference_entries = next_entries


class AsyncUserRepository(UserRepositoryPort):
    """Async SQLAlchemy user repository. Never calls session.commit()."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, user_domain: UserDomainModel) -> UserDomainModel:
        user_entity = UserMapper.to_persistence(user_domain)

        if user_entity.id is not None:
            result = await self.session.execute(
                select(User).options(*_USER_LOADS).where(User.id == str(user_entity.id))
            )
            existing_entity = result.scalars().first()
        else:
            existing_entity = None

        if existing_entity:
            _copy_user_save_columns(existing_entity, user_entity)
            user_entity = existing_entity
        else:
            user_entity.profiles = [
                UserProfileMapper.to_persistence(p) for p in user_domain.profiles
            ]
            self.session.add(user_entity)
        try:
            await self.session.flush()
            # Re-fetch with eager loading to satisfy lazy="raise" on profiles/subscriptions
            user_id_str = str(user_entity.id)
            result = await self.session.execute(
                select(User).options(*_USER_LOADS).where(User.id == user_id_str)
            )
            user_entity = result.scalars().first()
            return UserMapper.to_domain(user_entity)
        except IntegrityError as e:
            # Do NOT call session.rollback() — UoW.__aexit__ handles rollback
            error_msg = str(e.orig).lower() if e.orig else str(e).lower()
            if "email" in error_msg:
                raise FirebaseIdentityConflictError(
                    "A Firebase identity conflicts with an existing account"
                ) from e
            elif "firebase_uid" in error_msg:
                raise FirebaseIdentityConflictError(
                    "A Firebase identity conflicts with an existing account"
                ) from e
            else:
                raise ValueError(
                    "User with this email or username already exists"
                ) from e

    async def find_by_id(self, user_id: UUID) -> UserDomainModel | None:
        user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id
        result = await self.session.execute(
            select(User)
            .options(*_USER_LOADS)
            .where(User.id == user_id_str, User.is_active.is_(True))
        )
        entity = result.scalars().first()
        return UserMapper.to_domain(entity) if entity else None

    async def find_by_email(self, email: str) -> UserDomainModel | None:
        result = await self.session.execute(
            select(User)
            .options(*_USER_LOADS)
            .where(User.email == email, User.is_active.is_(True))
        )
        entity = result.scalars().first()
        return UserMapper.to_domain(entity) if entity else None

    async def find_by_firebase_uid(self, firebase_uid: str) -> UserDomainModel | None:
        result = await self.session.execute(
            select(User)
            .options(*_USER_LOADS)
            .where(User.firebase_uid == firebase_uid, User.is_active.is_(True))
        )
        entity = result.scalars().first()
        return UserMapper.to_domain(entity) if entity else None

    async def find_deleted_by_firebase_uid(
        self, firebase_uid: str
    ) -> UserDomainModel | None:
        result = await self.session.execute(
            select(User)
            .options(*_USER_LOADS)
            .where(User.firebase_uid == firebase_uid, User.is_active.is_(False))
        )
        entity = result.scalars().first()
        return UserMapper.to_domain(entity) if entity else None

    async def find_all(
        self, limit: int = 100, offset: int = 0
    ) -> list[UserDomainModel]:
        result = await self.session.execute(
            select(User)
            .options(*_USER_LOADS)
            .where(User.is_active.is_(True))
            .limit(limit)
            .offset(offset)
        )
        return [UserMapper.to_domain(u) for u in result.scalars().all()]

    async def delete(self, user_id: UUID) -> bool:
        user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id
        result = await self.session.execute(select(User).where(User.id == user_id_str))
        entity = result.scalars().first()
        if entity:
            entity.is_active = False
            await self.session.flush()
            return True
        return False

    async def get_profile(self, user_id: UUID) -> UserProfileDomainModel | None:
        user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id
        result = await self.session.execute(
            select(UserProfile)
            .options(selectinload(UserProfile.preference_entries))
            .where(UserProfile.user_id == user_id_str, UserProfile.is_current.is_(True))
        )
        entity = result.scalars().first()
        return UserProfileMapper.to_domain(entity) if entity else None

    _IMMUTABLE_COLS = {"id", "created_at", "updated_at"}

    async def update_profile(
        self, profile_domain: UserProfileDomainModel
    ) -> UserProfileDomainModel:
        profile_id_str = (
            str(profile_domain.id)
            if isinstance(profile_domain.id, UUID)
            else profile_domain.id
        )
        result = await self.session.execute(
            select(UserProfile)
            .options(selectinload(UserProfile.preference_entries))
            .where(UserProfile.id == profile_id_str)
        )
        entity = result.scalars().first()
        if not entity:
            entity = UserProfileMapper.to_persistence(profile_domain)
            self.session.add(entity)
        else:
            updated = UserProfileMapper.to_persistence(profile_domain)
            for col in UserProfile.__table__.columns:
                col_name = col.key
                if col_name not in self._IMMUTABLE_COLS:
                    new_val = getattr(updated, col_name, None)
                    old_val = getattr(entity, col_name, None)
                    # Only mark a column dirty when its value truly changes.
                    if new_val != old_val:
                        setattr(entity, col_name, new_val)
            _sync_profile_preference_entries(entity, profile_domain)

        # Data hygiene: legacy rows may have NULL timestamps despite NOT NULL constraints.
        # Backfill them so any subsequent UPDATE does not violate constraints.
        now = utc_now()
        if getattr(entity, "created_at", None) is None:
            entity.created_at = now
        if getattr(entity, "updated_at", None) is None:
            entity.updated_at = now
        await self.session.flush()
        await self.session.refresh(entity)
        return UserProfileMapper.to_domain(entity)

    async def update_user_timezone(self, user_id: UUID, timezone: str) -> None:
        await self.session.execute(
            update(User).where(User.id == str(user_id)).values(timezone=timezone)
        )

    async def get_user_timezone(self, user_id: UUID) -> str | None:
        result = await self.session.execute(
            select(User.timezone).where(
                User.id == str(user_id), User.is_active.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def update_user_language(self, user_id: UUID, language_code: str) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == str(user_id))
            .values(language_code=language_code)
        )

    async def get_weekly_auto_adjust(self, user_id: UUID) -> bool:
        result = await self.session.execute(
            select(User.weekly_auto_adjust).where(
                User.id == str(user_id), User.is_active.is_(True)
            )
        )
        value = result.scalar_one_or_none()
        return True if value is None else bool(value)

    async def update_user_weekly_auto_adjust(
        self, user_id: UUID, enabled: bool
    ) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == str(user_id))
            .values(weekly_auto_adjust=enabled)
        )

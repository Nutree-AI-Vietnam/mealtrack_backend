"""Integration tests for AsyncUserRepository."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from src.api.schemas.common.auth_enums import AuthProviderEnum
from src.domain.model.user import UserProfileDomainModel
from src.infra.database.models.user.profile_preference import UserProfilePreference
from src.infra.database.models.user.user import User
from src.infra.repositories.user_repository_async import AsyncUserRepository


async def _insert_user(session, uid: str = "firebase-uid-001") -> User:
    """Insert a minimal but valid User row (all non-nullable fields supplied)."""
    user = User(
        id=str(uuid.uuid4()),
        firebase_uid=uid,
        email=f"{uid}@test.com",
        username=f"user_{uid.replace('-', '_')}",
        password_hash="hashed_password",
        provider=AuthProviderEnum.GOOGLE,
        is_active=True,
        onboarding_completed=False,
        last_accessed=datetime.utcnow(),
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_find_by_firebase_uid_returns_active_user(async_db_session):
    await _insert_user(async_db_session, "firebase-uid-t01")
    repo = AsyncUserRepository(async_db_session)
    result = await repo.find_by_firebase_uid("firebase-uid-t01")
    assert result is not None
    assert result.firebase_uid == "firebase-uid-t01"


@pytest.mark.asyncio
async def test_find_by_firebase_uid_returns_none_for_inactive(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t02")
    user.is_active = False
    await async_db_session.flush()

    repo = AsyncUserRepository(async_db_session)
    result = await repo.find_by_firebase_uid("firebase-uid-t02")
    assert result is None


@pytest.mark.asyncio
async def test_find_deleted_by_firebase_uid_returns_inactive_user(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t03")
    user.is_active = False
    await async_db_session.flush()

    repo = AsyncUserRepository(async_db_session)
    result = await repo.find_deleted_by_firebase_uid("firebase-uid-t03")
    assert result is not None
    assert result.firebase_uid == "firebase-uid-t03"


@pytest.mark.asyncio
async def test_find_by_email_returns_active_user(async_db_session):
    await _insert_user(async_db_session, "firebase-uid-t04")
    repo = AsyncUserRepository(async_db_session)
    result = await repo.find_by_email("firebase-uid-t04@test.com")
    assert result is not None
    assert result.email == "firebase-uid-t04@test.com"


@pytest.mark.asyncio
async def test_delete_marks_user_inactive(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t05")
    user_id = uuid.UUID(user.id)

    repo = AsyncUserRepository(async_db_session)
    deleted = await repo.delete(user_id)
    assert deleted is True

    # Active lookup should return nothing
    result = await repo.find_by_firebase_uid("firebase-uid-t05")
    assert result is None


@pytest.mark.asyncio
async def test_get_user_timezone_returns_default(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t06")
    user_id = uuid.UUID(user.id)

    repo = AsyncUserRepository(async_db_session)
    tz = await repo.get_user_timezone(user_id)
    # server_default is 'UTC'; Python default is also 'UTC' (no value set explicitly)
    assert tz is not None


@pytest.mark.asyncio
async def test_update_user_timezone(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t07")
    user_id = uuid.UUID(user.id)

    repo = AsyncUserRepository(async_db_session)
    await repo.update_user_timezone(user_id, "America/New_York")
    await async_db_session.flush()

    tz = await repo.get_user_timezone(user_id)
    assert tz == "America/New_York"


@pytest.mark.asyncio
async def test_weekly_auto_adjust_defaults_enabled(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t09")
    user_id = uuid.UUID(user.id)

    repo = AsyncUserRepository(async_db_session)
    enabled = await repo.get_weekly_auto_adjust(user_id)
    assert enabled is True


@pytest.mark.asyncio
async def test_update_weekly_auto_adjust(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t10")
    user_id = uuid.UUID(user.id)

    repo = AsyncUserRepository(async_db_session)
    await repo.update_user_weekly_auto_adjust(user_id, False)
    await async_db_session.flush()

    enabled = await repo.get_weekly_auto_adjust(user_id)
    assert enabled is False


@pytest.mark.asyncio
async def test_update_profile_reuses_existing_preference_rows(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t08")
    repo = AsyncUserRepository(async_db_session)

    profile = UserProfileDomainModel(
        user_id=uuid.UUID(user.id),
        age=30,
        gender="female",
        height_cm=165,
        weight_kg=60,
        job_type="desk",
        training_days_per_week=3,
        training_minutes_per_session=45,
        fitness_goal="maintenance",
        meals_per_day=3,
        dietary_preferences=["classic"],
        pain_points=["No motivation"],
        referral_sources=["tiktok"],
        training_types=["swimming"],
    )

    saved = await repo.update_profile(profile)
    saved.age = 31
    saved.dietary_preferences = ["classic"]
    saved.pain_points = ["No motivation"]
    saved.referral_sources = ["tiktok"]
    saved.training_types = ["swimming"]

    updated = await repo.update_profile(saved)

    result = await async_db_session.execute(
        select(UserProfilePreference).where(
            UserProfilePreference.profile_id == str(updated.id)
        )
    )
    rows = result.scalars().all()
    assert updated.age == 31
    assert sorted((row.preference_type, row.value) for row in rows) == [
        ("dietary_preferences", "classic"),
        ("pain_points", "No motivation"),
        ("referral_sources", "tiktok"),
        ("training_types", "swimming"),
    ]


@pytest.mark.asyncio
async def test_save_existing_user_preserves_profile_preference_rows(async_db_session):
    user = await _insert_user(async_db_session, "firebase-uid-t09")
    repo = AsyncUserRepository(async_db_session)

    profile = UserProfileDomainModel(
        user_id=uuid.UUID(user.id),
        age=30,
        gender="female",
        height_cm=165,
        weight_kg=60,
        job_type="desk",
        training_days_per_week=3,
        training_minutes_per_session=45,
        fitness_goal="maintenance",
        meals_per_day=3,
        dietary_preferences=["classic"],
        pain_points=["No motivation"],
        referral_sources=["tiktok"],
        training_types=["swimming"],
    )
    saved_profile = await repo.update_profile(profile)

    loaded_user = await repo.find_by_firebase_uid("firebase-uid-t09")
    assert loaded_user is not None
    loaded_user.onboarding_completed = True
    loaded_user.last_accessed = datetime.utcnow()

    updated_user = await repo.save(loaded_user)

    result = await async_db_session.execute(
        select(UserProfilePreference).where(
            UserProfilePreference.profile_id == str(saved_profile.id)
        )
    )
    rows = result.scalars().all()
    assert updated_user.onboarding_completed is True
    assert sorted((row.preference_type, row.value) for row in rows) == [
        ("dietary_preferences", "classic"),
        ("pain_points", "No motivation"),
        ("referral_sources", "tiktok"),
        ("training_types", "swimming"),
    ]

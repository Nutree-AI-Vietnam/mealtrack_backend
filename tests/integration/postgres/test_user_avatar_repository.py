"""Avatar and reminder columns on real PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.domain.exceptions.user_exceptions import UserNotFoundError
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.notification import NotificationPreferences
from src.domain.model.user import UserDomainModel
from src.domain.model.user.user_avatar import AvatarKind, UserAvatar
from src.infra.repositories.notification_repository_async import (
    AsyncNotificationRepository,
)
from src.infra.repositories.user_repository_async import AsyncUserRepository

pytestmark = pytest.mark.integration


async def _new_user(session, name: str = "avatar") -> UserDomainModel:
    user = UserDomainModel(
        firebase_uid=f"uid-{name}-{uuid4()}",
        email=f"{name}-{uuid4()}@example.com",
        username=f"{name}-{uuid4()}"[:40],
        password_hash="",
        provider=AuthProvider.GOOGLE,
    )
    saved = await AsyncUserRepository(session).save(user)
    await session.commit()
    return saved


@pytest.mark.asyncio
async def test_new_user_reads_back_the_auto_avatar(pg_session):
    user = await _new_user(pg_session)

    assert user.avatar == UserAvatar()
    reloaded = await AsyncUserRepository(pg_session).find_by_id(user.id)
    assert reloaded is not None and reloaded.avatar.kind is AvatarKind.AUTO


@pytest.mark.asyncio
async def test_updater_stores_the_avatar_and_returns_the_previous_image_id(
    pg_session,
):
    repository = AsyncUserRepository(pg_session)
    user = await _new_user(pg_session)

    first = await repository.update_user_avatar(
        user.id, UserAvatar.photo("img-1", "https://img.example/img-1")
    )
    second = await repository.update_user_avatar(
        user.id, UserAvatar.chosen("character", character="carrot")
    )
    await pg_session.commit()

    assert first is None
    assert second == "img-1"
    pg_session.expire_all()
    reloaded = await repository.find_by_id(user.id)
    assert reloaded is not None
    assert reloaded.avatar == UserAvatar.chosen("character", character="carrot")


@pytest.mark.asyncio
async def test_saving_a_stale_model_leaves_the_avatar_unchanged(pg_session):
    repository = AsyncUserRepository(pg_session)
    stale = await _new_user(pg_session)
    await repository.update_user_avatar(
        stale.id, UserAvatar.chosen("initials", color=2)
    )
    await pg_session.commit()

    stale.display_name = "Renamed by sync"
    await repository.save(stale)
    await pg_session.commit()

    pg_session.expire_all()
    reloaded = await repository.find_by_id(stale.id)
    assert reloaded is not None
    assert reloaded.display_name == "Renamed by sync"
    assert reloaded.avatar == UserAvatar.chosen("initials", color=2)


@pytest.mark.asyncio
async def test_updater_rejects_a_missing_or_inactive_user(pg_session):
    repository = AsyncUserRepository(pg_session)
    user = await _new_user(pg_session)
    await repository.delete(user.id)
    await pg_session.commit()

    with pytest.raises(UserNotFoundError):
        await repository.update_user_avatar(user.id, UserAvatar())
    with pytest.raises(UserNotFoundError):
        await repository.update_user_avatar(str(uuid4()), UserAvatar())


@pytest.mark.asyncio
async def test_updater_locks_the_user_row(pg_session, async_session_factory):
    user = await _new_user(pg_session)
    await AsyncUserRepository(pg_session).update_user_avatar(user.id, UserAvatar())

    async with async_session_factory() as other:
        await other.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(DBAPIError, match="lock"):
            await AsyncUserRepository(other).update_user_avatar(
                user.id, UserAvatar.chosen("initials", color=1)
            )
        await other.rollback()
    await pg_session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assignment", ["avatar_color = 8", "avatar_kind = 'emoji'", "avatar_kind = NULL"]
)
async def test_database_checks_reject_invalid_avatar_values(pg_session, assignment):
    user = await _new_user(pg_session)

    with pytest.raises(IntegrityError):
        await pg_session.execute(
            text(f"UPDATE users SET {assignment} WHERE id = :id"), {"id": str(user.id)}
        )
    await pg_session.rollback()


@pytest.mark.asyncio
async def test_meal_switches_and_water_time_round_trip(pg_session):
    user = await _new_user(pg_session, "reminders")
    repository = AsyncNotificationRepository(pg_session)
    prefs = NotificationPreferences.create_default(str(user.id)).update_preferences(
        lunch_reminder_enabled=False, hydration_reminder_time_minutes=1000
    )

    await repository.save_notification_preferences(prefs)
    await pg_session.commit()
    pg_session.expire_all()
    stored = await repository.find_notification_preferences_by_user(str(user.id))

    assert stored is not None
    assert stored.breakfast_reminder_enabled is True
    assert stored.lunch_reminder_enabled is False
    assert stored.dinner_reminder_enabled is True
    assert stored.meal_reminders_enabled is True
    assert stored.hydration_reminder_time_minutes == 1000


@pytest.mark.asyncio
async def test_database_check_rejects_a_water_time_outside_the_day(pg_session):
    user = await _new_user(pg_session, "water")
    await AsyncNotificationRepository(pg_session).save_notification_preferences(
        NotificationPreferences.create_default(str(user.id))
    )
    await pg_session.commit()

    with pytest.raises(IntegrityError):
        await pg_session.execute(
            text(
                "UPDATE notification_preferences "
                "SET hydration_reminder_time_minutes = 1440 WHERE user_id = :id"
            ),
            {"id": str(user.id)},
        )
    await pg_session.rollback()

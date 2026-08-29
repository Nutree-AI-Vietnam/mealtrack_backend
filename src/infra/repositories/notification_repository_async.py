"""Async notification repository."""

import logging

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.notification import NotificationPreferences
from src.infra.database.models.notification.notification_preferences import (
    NotificationPreferencesORM,
)
from src.infra.mappers.notification_mapper import (
    notification_prefs_orm_to_domain,
)

logger = logging.getLogger(__name__)


class AsyncNotificationRepository:
    """Async notification repository. Never calls session.commit()."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # Notification Preferences operations
    # ------------------------------------------------------------------

    async def save_notification_preferences(
        self, preferences: NotificationPreferences
    ) -> NotificationPreferences:
        """Insert or update notification preferences."""
        result = await self.session.execute(
            select(NotificationPreferencesORM).where(
                NotificationPreferencesORM.user_id == preferences.user_id
            )
        )
        existing = result.scalars().first()

        if existing:
            existing.meal_reminders_enabled = preferences.meal_reminders_enabled
            existing.daily_summary_enabled = preferences.daily_summary_enabled
            existing.breakfast_time_minutes = preferences.breakfast_time_minutes
            existing.lunch_time_minutes = preferences.lunch_time_minutes
            existing.dinner_time_minutes = preferences.dinner_time_minutes
            existing.daily_summary_time_minutes = preferences.daily_summary_time_minutes
            existing.language = preferences.language
            existing.updated_at = preferences.updated_at
            await self.session.flush()
            return notification_prefs_orm_to_domain(existing)
        else:
            db_prefs = NotificationPreferencesORM(
                id=preferences.preferences_id,
                user_id=preferences.user_id,
                meal_reminders_enabled=preferences.meal_reminders_enabled,
                daily_summary_enabled=preferences.daily_summary_enabled,
                breakfast_time_minutes=preferences.breakfast_time_minutes,
                lunch_time_minutes=preferences.lunch_time_minutes,
                dinner_time_minutes=preferences.dinner_time_minutes,
                daily_summary_time_minutes=preferences.daily_summary_time_minutes,
                language=preferences.language,
                created_at=preferences.created_at,
                updated_at=preferences.updated_at,
            )
            self.session.add(db_prefs)
            await self.session.flush()
            return notification_prefs_orm_to_domain(db_prefs)

    async def find_notification_preferences_by_user(
        self, user_id: str
    ) -> NotificationPreferences | None:
        """Find notification preferences by user ID."""
        result = await self.session.execute(
            select(NotificationPreferencesORM).where(
                NotificationPreferencesORM.user_id == user_id
            )
        )
        db_prefs = result.scalars().first()
        return notification_prefs_orm_to_domain(db_prefs) if db_prefs else None

    async def update_notification_preferences(
        self, user_id: str, preferences: NotificationPreferences
    ) -> NotificationPreferences:
        """Update notification preferences for a user (delegates to save)."""
        return await self.save_notification_preferences(preferences)

    async def update_notification_language(self, user_id: str, language: str) -> int:
        """Update the notification language for an existing preferences row."""
        result = await self.session.execute(
            update(NotificationPreferencesORM)
            .where(
                and_(
                    NotificationPreferencesORM.user_id == user_id,
                    NotificationPreferencesORM.is_deleted.is_(False),
                )
            )
            .values(language=language)
        )
        await self.session.flush()
        return result.rowcount or 0

    async def delete_notification_preferences(self, user_id: str) -> bool:
        """Delete notification preferences for a user."""
        result = await self.session.execute(
            select(NotificationPreferencesORM).where(
                NotificationPreferencesORM.user_id == user_id
            )
        )
        db_prefs = result.scalars().first()
        if db_prefs:
            await self.session.delete(db_prefs)
            await self.session.flush()
            return True
        return False

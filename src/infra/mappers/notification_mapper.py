"""Notification ORM <-> domain mapping functions."""

from src.domain.model.notification import (
    NotificationPreferences,
)
from src.infra.database.models.notification.notification_preferences import (
    NotificationPreferencesORM,
)


def notification_prefs_orm_to_domain(
    orm: NotificationPreferencesORM,
) -> NotificationPreferences:
    return NotificationPreferences(
        preferences_id=orm.id,
        user_id=orm.user_id,
        meal_reminders_enabled=orm.meal_reminders_enabled,
        breakfast_reminder_enabled=orm.breakfast_reminder_enabled,
        lunch_reminder_enabled=orm.lunch_reminder_enabled,
        dinner_reminder_enabled=orm.dinner_reminder_enabled,
        daily_summary_enabled=orm.daily_summary_enabled,
        hydration_reminders_enabled=orm.hydration_reminders_enabled,
        streak_reminders_enabled=orm.streak_reminders_enabled,
        reengagement_enabled=orm.reengagement_enabled,
        progress_updates_enabled=orm.progress_updates_enabled,
        offers_enabled=orm.offers_enabled,
        breakfast_time_minutes=orm.breakfast_time_minutes,
        lunch_time_minutes=orm.lunch_time_minutes,
        dinner_time_minutes=orm.dinner_time_minutes,
        daily_summary_time_minutes=orm.daily_summary_time_minutes,
        hydration_reminder_time_minutes=orm.hydration_reminder_time_minutes,
        language=orm.language,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
    )

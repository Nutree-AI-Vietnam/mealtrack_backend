"""
Notification preferences model for user notification settings.
"""

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class NotificationPreferencesORM(Base, BaseMixin):
    """Notification preferences table for user notification settings."""

    __tablename__ = "notification_preferences"

    # User relationship (one-to-one)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Notification Type Toggles
    # meal_reminders_enabled is derived: true while any meal switch below is on.
    # Older apps still read and send it.
    meal_reminders_enabled = Column(Boolean, default=True, nullable=False)
    breakfast_reminder_enabled = Column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    lunch_reminder_enabled = Column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    dinner_reminder_enabled = Column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    daily_summary_enabled = Column(Boolean, default=True, nullable=False)
    hydration_reminders_enabled = Column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    # Engagement toggles; the device plans these notifications itself.
    # Streak saver and "win the day".
    streak_reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    # Lapse ladder ("comeback nudges").
    reengagement_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    # Weekly recap, protein gap and other progress notes.
    progress_updates_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default="true"
    )
    # Promotional items need an opt-in (App Store guideline 4.5.4).
    offers_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Meal Reminder Timing (minutes from midnight: 0-1439)
    breakfast_time_minutes = Column(Integer, nullable=True)
    lunch_time_minutes = Column(Integer, nullable=True)
    dinner_time_minutes = Column(Integer, nullable=True)

    # Daily Summary Timing (minutes from midnight)
    daily_summary_time_minutes = Column(Integer, nullable=True, default=1260)

    # Water reminder time (minutes from midnight), 15:30 by default
    hydration_reminder_time_minutes = Column(
        Integer, default=930, nullable=False, server_default="930"
    )

    # Preferred notification language (ISO 639-1: 'en', 'vi')
    language = Column(String(5), default="en", nullable=False, server_default="en")

    # Soft delete flag
    is_deleted = Column(Boolean, default=False, nullable=False)

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "breakfast_time_minutes >= 0 AND breakfast_time_minutes < 1440",
            name="check_breakfast_time",
        ),
        CheckConstraint(
            "lunch_time_minutes >= 0 AND lunch_time_minutes < 1440",
            name="check_lunch_time",
        ),
        CheckConstraint(
            "dinner_time_minutes >= 0 AND dinner_time_minutes < 1440",
            name="check_dinner_time",
        ),
        CheckConstraint(
            "daily_summary_time_minutes >= 0 AND daily_summary_time_minutes < 1440",
            name="check_daily_summary_time",
        ),
        CheckConstraint(
            "hydration_reminder_time_minutes >= 0 "
            "AND hydration_reminder_time_minutes < 1440",
            name="check_hydration_reminder_time",
        ),
    )

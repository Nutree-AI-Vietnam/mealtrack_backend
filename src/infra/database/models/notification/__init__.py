"""Notification database models."""

from .notification_preferences import NotificationPreferencesORM
from .user_fcm_token import UserFcmTokenORM

__all__ = [
    "NotificationPreferencesORM",
    "UserFcmTokenORM",
]

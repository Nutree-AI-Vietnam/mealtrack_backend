"""
Notification domain models.
"""

from .enums import DeviceType, NotificationType
from .notification_preferences import NotificationPreferences

__all__ = [
    "DeviceType",
    "NotificationType",
    "NotificationPreferences",
]

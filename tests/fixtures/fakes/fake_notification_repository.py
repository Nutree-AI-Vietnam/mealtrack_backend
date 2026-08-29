from src.domain.model.notification import NotificationPreferences
from src.domain.ports.notification_repository_port import NotificationRepositoryPort


class FakeNotificationRepository(NotificationRepositoryPort):
    def __init__(self):
        self.preferences = {}  # user_id -> NotificationPreferences

    # Notification Preferences operations
    async def save_notification_preferences(
        self, preferences: NotificationPreferences
    ) -> NotificationPreferences:
        self.preferences[preferences.user_id] = preferences
        return preferences

    async def find_notification_preferences_by_user(
        self, user_id: str
    ) -> NotificationPreferences | None:
        return self.preferences.get(user_id)

    async def update_notification_preferences(
        self, user_id: str, preferences: NotificationPreferences
    ) -> NotificationPreferences:
        self.preferences[user_id] = preferences
        return preferences

    async def update_notification_language(self, user_id: str, language: str) -> int:
        prefs = self.preferences.get(user_id)
        if prefs is None:
            return 0
        self.preferences[user_id] = prefs.update_preferences(language=language)
        return 1

    async def delete_notification_preferences(self, user_id: str) -> bool:
        if user_id in self.preferences:
            del self.preferences[user_id]
            return True
        return False

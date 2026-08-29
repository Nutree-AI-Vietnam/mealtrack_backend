from abc import ABC, abstractmethod

from src.domain.model.notification import NotificationPreferences


class NotificationRepositoryPort(ABC):
    """Port interface for notification persistence operations."""

    # Notification Preferences operations
    @abstractmethod
    async def save_notification_preferences(
        self, preferences: NotificationPreferences
    ) -> NotificationPreferences:
        """
        Persists notification preferences.

        Args:
            preferences: The notification preferences to be saved

        Returns:
            The saved notification preferences with any generated IDs
        """
        pass

    @abstractmethod
    async def find_notification_preferences_by_user(
        self, user_id: str
    ) -> NotificationPreferences | None:
        """
        Finds notification preferences by user ID.

        Args:
            user_id: The user ID to find preferences for

        Returns:
            The notification preferences if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_notification_preferences(
        self, user_id: str, preferences: NotificationPreferences
    ) -> NotificationPreferences:
        """
        Updates notification preferences for a user.

        Args:
            user_id: The user ID to update preferences for
            preferences: The updated notification preferences

        Returns:
            The updated notification preferences
        """
        pass

    @abstractmethod
    async def update_notification_language(self, user_id: str, language: str) -> int:
        """
        Updates the notification language for an existing preferences row.

        Args:
            user_id: The user ID to update preferences for
            language: ISO 639-1 notification language

        Returns:
            Number of updated rows
        """
        pass

    @abstractmethod
    async def delete_notification_preferences(self, user_id: str) -> bool:
        """
        Deletes notification preferences for a user.

        Args:
            user_id: The user ID to delete preferences for

        Returns:
            True if preferences were found and deleted, False otherwise
        """
        pass

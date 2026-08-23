"""
Handler for getting notification preferences.
"""

import logging
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.notification import GetNotificationPreferencesQuery
from src.domain.model.notification import NotificationPreferences
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetNotificationPreferencesQuery)
class GetNotificationPreferencesQueryHandler(
    EventHandler[GetNotificationPreferencesQuery, dict[str, Any]]
):
    """Handler for getting notification preferences."""

    def __init__(self):
        pass

    def set_dependencies(self, **kwargs):
        """Set dependencies for dependency injection."""
        pass

    async def handle(self, query: GetNotificationPreferencesQuery) -> dict[str, Any]:
        """Handle notification preferences query."""
        return await self._compute(query)


    async def _compute(self, query: GetNotificationPreferencesQuery) -> dict[str, Any]:
        """Fetch notification preferences from DB."""
        async with AsyncUnitOfWork() as uow:
            # Get preferences for user
            preferences = (
                await uow.notifications.find_notification_preferences_by_user(
                    query.user_id
                )
            )

            if not preferences:
                # Create and return default preferences
                default_prefs = NotificationPreferences.create_default(
                    query.user_id
                )
                saved_prefs = await uow.notifications.save_notification_preferences(
                    default_prefs
                )
                await uow.commit()

                logger.info(
                    f"Created default notification preferences for user {query.user_id}"
                )
                return saved_prefs.to_dict()
            else:
                return preferences.to_dict()


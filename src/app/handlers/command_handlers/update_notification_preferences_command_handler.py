"""Handler for updating notification preferences."""

import logging
from typing import Any

from src.app.commands.notification import UpdateNotificationPreferencesCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import (
    UserProfileUpdatedEvent,
)
from src.domain.model.notification import NotificationPreferences
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(UpdateNotificationPreferencesCommand)
class UpdateNotificationPreferencesCommandHandler(
    EventHandler[UpdateNotificationPreferencesCommand, dict[str, Any]]
):
    """Handler for updating notification preferences."""

    def __init__(
        self,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        **kwargs: Any,
    ):
        self.event_publisher = event_publisher
        self.environment = environment

    def set_dependencies(self, **kwargs):
        """Set dependencies for dependency injection."""
        if "event_publisher" in kwargs:
            self.event_publisher = kwargs["event_publisher"]

    async def handle(
        self, command: UpdateNotificationPreferencesCommand
    ) -> dict[str, Any]:
        """Handle notification preferences update."""
        try:
            async with AsyncUnitOfWork() as uow:
                existing_prefs = (
                    await uow.notifications.find_notification_preferences_by_user(
                        command.user_id
                    )
                )

                if not existing_prefs:
                    existing_prefs = NotificationPreferences.create_default(
                        command.user_id
                    )
                    saved_prefs = await uow.notifications.save_notification_preferences(
                        existing_prefs
                    )
                else:
                    saved_prefs = existing_prefs

                updated_prefs = saved_prefs.update_preferences(
                    meal_reminders_enabled=command.meal_reminders_enabled,
                    daily_summary_enabled=command.daily_summary_enabled,
                    hydration_reminders_enabled=command.hydration_reminders_enabled,
                    breakfast_time_minutes=command.breakfast_time_minutes,
                    lunch_time_minutes=command.lunch_time_minutes,
                    dinner_time_minutes=command.dinner_time_minutes,
                    daily_summary_time_minutes=command.daily_summary_time_minutes,
                    language=command.language,
                )

                final_prefs = await uow.notifications.save_notification_preferences(
                    updated_prefs
                )
                await uow.commit()

                logger.info(
                    f"Notification preferences updated for user {command.user_id}"
                )
                result = {"success": True, "preferences": final_prefs.to_dict()}

        except Exception as e:
            raise e

        event = UserProfileUpdatedEvent(
            environment=self.environment,
            aggregate_id=str(command.user_id),
            data={
                "user_id": str(command.user_id),
                "notification_preferences": final_prefs.to_dict(),
            },
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())

        return result

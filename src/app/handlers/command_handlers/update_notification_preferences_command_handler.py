"""
Handler for updating notification preferences.
"""

import inspect
import logging
from typing import Any

from src.app.commands.notification import UpdateNotificationPreferencesCommand
from src.app.events.base import EventHandler, handles
from src.app.services.background_job_scheduler import schedule_background_job
from src.domain.model.notification import NotificationPreferences
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.services.daily_context_precompute_service import (
    DailyContextPrecomputeService,
)

logger = logging.getLogger(__name__)


@handles(UpdateNotificationPreferencesCommand)
class UpdateNotificationPreferencesCommandHandler(
    EventHandler[UpdateNotificationPreferencesCommand, dict[str, Any]]
):
    """Handler for updating notification preferences."""

    def __init__(
        self,
        precompute_service: DailyContextPrecomputeService | None = None,
        task_manager: Any | None = None,
    ):
        self.precompute_service = precompute_service
        self.task_manager = task_manager

    def set_dependencies(self, **kwargs):
        """Set dependencies for dependency injection."""
        if "precompute_service" in kwargs:
            self.precompute_service = kwargs["precompute_service"]

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
                outbox = getattr(uow, "outbox", None)
                if outbox is not None and inspect.iscoroutinefunction(
                    getattr(outbox, "enqueue", None)
                ):
                    await outbox.enqueue(
                        "notification_reschedule",
                        {"user_id": command.user_id, "reason": "preferences"},
                        event_id=(
                            f"notification-reschedule:preferences:{command.user_id}:"
                            f"{final_prefs.updated_at.isoformat()}"
                        ),
                        aggregate_type="user",
                        aggregate_id=command.user_id,
                    )
                await uow.commit()

                logger.info(
                    f"Notification preferences updated for user {command.user_id}"
                )
                result = {"success": True, "preferences": final_prefs.to_dict()}

        except Exception as e:
            raise e

        self._schedule_notification_reschedule(command.user_id, "preferences")

        return result


    def _schedule_notification_reschedule(self, user_id: str, reason: str) -> None:
        if self.precompute_service is None:
            return
        schedule_background_job(
            self.task_manager,
            f"notifications:reschedule:{user_id}:{reason}",
            self.precompute_service.reschedule_user_notifications(user_id),
            logger=logger,
        )

"""
Handler for registering FCM tokens.
"""

import logging
from typing import Any

from src.app.commands.notification import RegisterFcmTokenCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import (
    UserProfileUpdatedEvent,
)
from src.domain.model.notification import DeviceType, UserFcmToken
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.notification_repository_port import NotificationRepositoryPort
from src.domain.utils.timezone_utils import is_valid_timezone, normalize_timezone
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(RegisterFcmTokenCommand)
class RegisterFcmTokenCommandHandler(
    EventHandler[RegisterFcmTokenCommand, dict[str, Any]]
):
    """Handler for registering FCM tokens."""

    def __init__(
        self,
        notification_repository: NotificationRepositoryPort = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
        **kwargs: Any,
    ):
        self.notification_repository = notification_repository
        self.event_publisher = event_publisher
        self.environment = environment

    def set_dependencies(self, **kwargs):
        """Set dependencies for dependency injection."""
        if "notification_repository" in kwargs:
            self.notification_repository = kwargs["notification_repository"]
        if "event_publisher" in kwargs:
            self.event_publisher = kwargs["event_publisher"]

    async def handle(self, command: RegisterFcmTokenCommand) -> dict[str, Any]:
        """Handle FCM token registration with old token cleanup."""
        timezone_changed = False
        async with AsyncUnitOfWork() as uow:
            # Use notification repository from UoW if not injected
            notification_repo = self.notification_repository or uow.notifications

            device_type = (
                DeviceType.IOS if command.device_type == "ios" else DeviceType.ANDROID
            )

            # 1. Deactivate OLD tokens for this user+device (token refresh scenario)
            existing_tokens = await notification_repo.find_active_fcm_tokens_by_user(
                command.user_id
            )
            deactivated_count = 0
            for old_token in existing_tokens:
                # Deactivate tokens of same device type (new token replaces old)
                if (
                    old_token.device_type == device_type
                    and old_token.fcm_token != command.fcm_token
                ):
                    await notification_repo.deactivate_fcm_token(old_token.fcm_token)
                    deactivated_count += 1
                    logger.info(f"Deactivated old FCM token for user {command.user_id}")

            # 2. Create/update new token
            fcm_token = UserFcmToken.create_new(
                user_id=command.user_id,
                fcm_token=command.fcm_token,
                device_type=device_type,
            )

            saved_token = await notification_repo.save_fcm_token(fcm_token)

            # 3. Update user timezone if provided
            if command.timezone and is_valid_timezone(command.timezone):
                canonical_tz = normalize_timezone(command.timezone)
                current_tz = await uow.users.get_user_timezone(command.user_id)
                if current_tz != canonical_tz:
                    await uow.users.update_user_timezone(command.user_id, canonical_tz)
                    timezone_changed = True
                    logger.info(
                        f"Updated timezone for user {command.user_id}: {canonical_tz}"
                    )
            elif command.timezone:
                logger.warning(
                    "Invalid timezone ignored during FCM registration: %r for user %s",
                    command.timezone,
                    command.user_id,
                )

            # UoW auto-commits on exit

        if timezone_changed and self.event_publisher is not None:
            try:
                event = UserProfileUpdatedEvent(
                    environment=self.environment,
                    aggregate_id=str(command.user_id),
                    data={
                        "user_id": str(command.user_id),
                        "timezone": command.timezone,
                    },
                )
                await self.event_publisher.publish(event.to_payload())
            except Exception as exc:
                logger.error("Failed to publish user profile updated event: %s", exc)

        logger.info(
            f"FCM token registered for user {command.user_id}, "
            f"deactivated {deactivated_count} old tokens"
        )

        return {
            "success": True,
            "message": "Token registered successfully",
            "token_id": saved_token.token_id,
            "deactivated_old_tokens": deactivated_count,
        }

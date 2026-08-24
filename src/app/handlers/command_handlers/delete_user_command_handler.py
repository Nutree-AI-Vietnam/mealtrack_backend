"""
DeleteUserCommandHandler - Handler for deleting user accounts.
Performs soft delete in database and emits UserDeletedEvent for async cleanup.
"""

import logging
from typing import Any

from src.api.dependencies.auth_cache import invalidate_cached_user_id
from src.api.exceptions import ResourceNotFoundException
from src.app.commands.user import DeleteUserCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_deleted_event import UserDeletedEvent
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.cache_port import CachePort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.enums import MealStatusEnum

# Models for soft-delete operations
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.notification.notification_preferences import (
    NotificationPreferencesORM as NotificationPreferences,
)
from src.infra.database.models.notification.user_fcm_token import (
    UserFcmTokenORM as UserFcmToken,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(DeleteUserCommand)
class DeleteUserCommandHandler(EventHandler[DeleteUserCommand, dict[str, Any]]):
    """Handler for deleting user accounts."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        cache_service: CachePort | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.cache_service = cache_service
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: DeleteUserCommand) -> dict[str, Any]:
        """
        Delete user account.
        - Soft delete in database (set is_active=False)
        - Anonymize user data
        - Emit UserDeletedEvent for out-of-process cleanup (nutreeai_async)
        """
        # Use provided UoW or create default
        uow = self.uow or AsyncUnitOfWork()

        async with uow:
            try:
                # Find user by firebase_uid
                user = await uow.users.find_by_firebase_uid(command.firebase_uid)

                if not user or not user.is_active:
                    raise ResourceNotFoundException(
                        "Active user with Firebase UID not found"
                    )

                # Store user_id for logging
                user_id = user.id

                # Step 1: Soft-delete all related user data
                await self._soft_delete_related_data(uow, str(user_id))

                # Step 2: Anonymize user data (GDPR compliance)
                user.username = f"deleted_user_{user.id}"
                user.first_name = None
                user.last_name = None
                user.display_name = None
                user.photo_url = None
                user.password_hash = "DELETED"

                # Step 3: Soft delete user with timestamp
                user.is_active = False
                user.deleted_at = utc_now()
                user.last_accessed = utc_now()

                # Save changes
                await uow.users.save(user)
                await uow.commit()
                logger.info("Successfully soft deleted user in database")

            except ResourceNotFoundException:
                # Re-raise not found errors
                raise
            except Exception as e:
                await uow.rollback()
                raise Exception(f"Failed to delete user account: {str(e)}") from e

        await invalidate_cached_user_id(self.cache_service, command.firebase_uid)

        # Step 4: Emit UserDeletedEvent to async queue for nutreeai_async to perform Firebase cleanup
        if self.event_publisher is not None:
            try:
                event = UserDeletedEvent(
                    environment=self.environment,
                    aggregate_id=str(user_id),
                    data={
                        "user_id": str(user_id),
                        "firebase_uid": command.firebase_uid,
                    },
                )
                await self.event_publisher.publish(event.to_payload())
                logger.info(
                    "Published user deleted integration event event_id=%s aggregate_id=%s",
                    event.event_id,
                    event.aggregate_id,
                )
            except Exception as exc:
                logger.error(
                    "Failed to publish user deleted event user_id=%s error=%s",
                    user_id,
                    exc,
                )

        return {
            "firebase_uid": command.firebase_uid,
            "deleted": True,
            "firebase_deleted": False,
            "tokens_revoked": False,
            "firebase_cleanup_queued": self.event_publisher is not None,
            "message": "Account successfully deleted; Firebase cleanup queued",
        }

    async def _soft_delete_related_data(self, uow, user_id: str) -> None:
        """
        Soft-delete all data related to the user.
        Uses bulk updates for performance. All operations are atomic within the transaction.
        """
        from sqlalchemy import update as sa_update

        try:
            # 1. Soft-delete meals (set status=INACTIVE)
            meals_result = await uow.session.execute(
                sa_update(MealORM)
                .where(MealORM.user_id == user_id)
                .values(status=MealStatusEnum.INACTIVE)
            )
            meals_count = meals_result.rowcount

            # 2. Soft-delete meal plans - no longer applicable (feature removed)
            meal_plans_count = 0

            # 3. Deactivate FCM tokens (set is_active=False)
            fcm_result = await uow.session.execute(
                sa_update(UserFcmToken)
                .where(UserFcmToken.user_id == user_id)
                .values(is_active=False)
            )
            fcm_tokens_count = fcm_result.rowcount

            # 4. Mark notification preferences as deleted (set is_deleted=True)
            notif_result = await uow.session.execute(
                sa_update(NotificationPreferences)
                .where(NotificationPreferences.user_id == user_id)
                .values(is_deleted=True)
            )
            notif_prefs_count = notif_result.rowcount

            # Flush to ensure all changes are pending in the transaction
            await uow.session.flush()

            logger.info(
                f"Soft-deleted related data: meals={meals_count}, meal_plans={meal_plans_count}, "
                f"fcm_tokens={fcm_tokens_count}, notification_prefs={notif_prefs_count}"
            )
        except Exception:
            raise

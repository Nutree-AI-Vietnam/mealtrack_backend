"""
DeleteUserCommandHandler - Handler for deleting user accounts.
Deletes the linked Open Wearables user, performs soft delete in database and
emits UserDeletedEvent for async cleanup.
"""

import logging
from typing import Any
from uuid import UUID

from src.api.dependencies.auth_cache import invalidate_cached_user_id
from src.api.exceptions import ResourceNotFoundException
from src.app.commands.user import DeleteUserCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_deleted_event import UserDeletedEvent
from src.domain.model.user.firebase_identity import tombstone_firebase_uid
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.cache_port import CachePort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.ports.wearable_account_port import WearableAccountPort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.enums import MealStatusEnum

# Models for soft-delete operations
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.notification.notification_preferences import (
    NotificationPreferencesORM as NotificationPreferences,
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
        wearable_accounts: WearableAccountPort | None = None,
    ):
        self.uow = uow
        self.cache_service = cache_service
        self.event_publisher = event_publisher
        self.environment = environment
        self.wearable_accounts = wearable_accounts

    async def handle(self, command: DeleteUserCommand) -> dict[str, Any]:
        """
        Delete user account.
        - Delete the linked Open Wearables user (health data, provider tokens)
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

                # Step 1: Delete the Open Wearables user before any commit.
                await self._delete_wearable_account(uow, user_id)

                # Step 2: Soft-delete all related user data
                await self._soft_delete_related_data(uow, str(user_id))

                # Step 3: Anonymize user data (GDPR compliance).
                # firebase_uid stays on the integration event (command.firebase_uid)
                # so Firebase cleanup still runs, but the column must be released.
                # ix_users_firebase_uid is unique for inactive rows too.
                user.email = f"deleted_{user.id}@deleted.local"
                user.username = f"deleted_user_{user.id}"
                user.firebase_uid = tombstone_firebase_uid(user.id)
                user.first_name = None
                user.last_name = None
                user.phone_number = None
                user.display_name = None
                user.photo_url = None
                user.password_hash = "DELETED"

                # Step 4: Soft delete user with timestamp
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

        # Step 5: Emit UserDeletedEvent to async queue for nutreeai_async to perform Firebase cleanup
        event = UserDeletedEvent(
            environment=self.environment,
            aggregate_id=str(user_id),
            data={
                "user_id": str(user_id),
                "firebase_uid": command.firebase_uid,
            },
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())
        logger.info(
            "Published user deleted integration event event_id=%s aggregate_id=%s",
            event.event_id,
            event.aggregate_id,
        )

        return {
            "firebase_uid": command.firebase_uid,
            "deleted": True,
            "firebase_deleted": False,
            "tokens_revoked": False,
            "firebase_cleanup_queued": True,
            "message": "Account successfully deleted; Firebase cleanup queued",
        }

    async def _delete_wearable_account(
        self, uow: AsyncUnitOfWorkPort, user_id: UUID
    ) -> None:
        """
        Delete the linked Open Wearables user and drop the link.
        Runs inside the transaction: a failure aborts the account deletion, so
        the person can retry while the link still exists. The UserDeletedEvent
        path is best effort and can drop events, so it cannot do this.
        """
        ow_user_id = await uow.users.find_open_wearables_user_id(user_id)
        if ow_user_id is None:
            return

        if self.wearable_accounts is None:
            # Keep the link on the deleted row so a later cleanup can find it.
            logger.warning(
                "Open Wearables is not configured; kept the link for cleanup "
                "user_id=%s",
                user_id,
            )
            return

        deleted = await self.wearable_accounts.delete_user(ow_user_id)
        if not deleted:
            logger.info("Open Wearables user was already deleted user_id=%s", user_id)
        await uow.users.clear_open_wearables_user_id(user_id)

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

            # 3. Mark notification preferences as deleted (set is_deleted=True)
            notif_result = await uow.session.execute(
                sa_update(NotificationPreferences)
                .where(NotificationPreferences.user_id == user_id)
                .values(is_deleted=True)
            )
            notif_prefs_count = notif_result.rowcount

            chat_count = 0
            if getattr(uow, "chat", None) is not None:
                await uow.chat.delete_user_chat(user_id)
                chat_count = 1

            # Flush to ensure all changes are pending in the transaction
            await uow.session.flush()

            logger.info(
                f"Soft-deleted related data: meals={meals_count}, "
                f"notification_prefs={notif_prefs_count}, chat={chat_count}"
            )
        except Exception:
            raise

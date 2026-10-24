"""
DeleteUserCommandHandler - Handler for deleting user accounts.
Performs soft delete in database, emits UserDeletedEvent for async cleanup,
then deletes the linked Open Wearables user.
"""

import logging
from typing import Any

from src.api.dependencies.auth_cache import invalidate_cached_user_id
from src.api.exceptions import ResourceNotFoundException
from src.app.commands.user import DeleteUserCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_deleted_event import UserDeletedEvent
from src.app.services.open_wearables_account_cleanup import (
    delete_open_wearables_user,
)
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
        - Soft delete in database (set is_active=False)
        - Anonymize user data
        - Emit UserDeletedEvent for out-of-process cleanup (nutreeai_async)
        - Delete the linked Open Wearables user (health data, provider tokens)
          after the commit, so an Open Wearables failure cannot undo the rest
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

                # The link stays on the deleted row until Open Wearables
                # confirms the deletion after the commit.
                ow_user_id = await uow.users.find_open_wearables_user_id(user_id)

                # Step 1: Soft-delete all related user data
                await self._soft_delete_related_data(uow, str(user_id))

                # Step 2: Anonymize user data (GDPR compliance).
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

        # Step 5: Delete the Open Wearables user now that the account is gone.
        # A failure keeps the link on the deleted row for the retry script.
        if ow_user_id is not None:
            await delete_open_wearables_user(
                self.wearable_accounts, uow, user_id, ow_user_id
            )

        return {
            "firebase_uid": command.firebase_uid,
            "deleted": True,
            "firebase_deleted": False,
            "tokens_revoked": False,
            "firebase_cleanup_queued": True,
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

            # Weekly recaps are derived stories that greet the user by name.
            recaps_count = 0
            if getattr(uow, "weekly_recaps", None) is not None:
                recaps_count = await uow.weekly_recaps.delete_for_user(user_id)

            # Flush to ensure all changes are pending in the transaction
            await uow.session.flush()

            logger.info(
                f"Soft-deleted related data: meals={meals_count}, "
                f"notification_prefs={notif_prefs_count}, chat={chat_count}, "
                f"weekly_recaps={recaps_count}"
            )
        except Exception:
            raise

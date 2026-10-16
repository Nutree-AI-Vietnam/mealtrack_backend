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
    require_event_publisher,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.enums import MealStatusEnum

# Models for soft-delete operations
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.notification.notification_preferences import (
    NotificationPreferencesORM as NotificationPreferences,
)
from src.infra.database.uow_async import AsyncUnitOfWork
from src.observability import increment_metric

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
                await self._retire_billing_identities_best_effort(
                    uow, str(user_id), command.firebase_uid
                )

                # Step 2: Anonymize user data (GDPR compliance)
                user.email = f"deleted_{user.id}@deleted.local"
                user.username = f"deleted_user_{user.id}"
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

        return {
            "firebase_uid": command.firebase_uid,
            "deleted": True,
            "firebase_deleted": False,
            "tokens_revoked": False,
            "firebase_cleanup_queued": True,
            "message": "Account successfully deleted; Firebase cleanup queued",
        }

    async def _retire_billing_identities_best_effort(
        self, uow: AsyncUnitOfWorkPort, user_id: str, firebase_uid: str
    ) -> None:
        """Run the ledger step in a SAVEPOINT so it can never block a deletion
        (e.g. ledger tables not migrated yet, or dropped by a rollback). The
        webhook and worker resolve only active users, so a skipped step leaves
        nothing that grants access."""
        try:
            async with uow.session.begin_nested():
                await self._retire_billing_identities(uow, user_id, firebase_uid)
        except Exception:
            increment_metric("entitlement.delete_hook.failed")
            logger.exception(
                "Billing identity retirement failed; account deletion continues"
            )

    async def _retire_billing_identities(
        self, uow: AsyncUnitOfWorkPort, user_id: str, firebase_uid: str
    ) -> None:
        """Entitlement ledger (D9, D11): drop stored webhook bodies and retire
        the billing identities, so the account's ids stop resolving to it and
        its syncs stop. Store rows and projections stay for audit."""
        # The sync's user lock: an in-flight sync either finishes first (and
        # its identities are retired here) or sees the inactive user and
        # writes nothing.
        await uow.user_entitlements.lock_users([user_id])
        identities = await uow.billing_identities.list_for_user(
            user_id, include_retired=True
        )
        purged = await uow.billing_events.purge_raw_for_user(
            user_id,
            app_user_ids=sorted(
                {firebase_uid, *(i.provider_customer_id for i in identities)}
            ),
        )
        retired = await uow.billing_identities.retire_for_user(
            user_id, retired_at=utc_now()
        )
        logger.info(
            "Retired billing identities: identities=%d, inbox_bodies_purged=%d",
            retired,
            purged,
        )

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

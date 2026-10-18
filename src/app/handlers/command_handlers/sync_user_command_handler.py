"""
SyncUserCommandHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
import re
from typing import Any

from src.app.commands.user.sync_user_command import SyncUserCommand
from src.app.events.base import EventHandler, handles
from src.domain.exceptions.firebase_identity_exceptions import (
    FirebaseIdentityConflictError,
)
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.notification import NotificationPreferences
from src.domain.model.user import UserDomainModel
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(SyncUserCommand)
class SyncUserCommandHandler(EventHandler[SyncUserCommand, dict[str, Any]]):
    """Handler for syncing user data from Firebase authentication."""

    def __init__(self, uow: AsyncUnitOfWorkPort | None = None):
        self.uow = uow

    async def handle(self, command: SyncUserCommand) -> dict[str, Any]:
        """Sync user data from Firebase authentication."""
        # Use provided UoW or create default
        uow = self.uow or AsyncUnitOfWork()

        # Using 'async with uow' manages the transaction scope
        async with uow:
            try:
                # One lookup covers both active accounts and soft-deleted rows.
                # firebase_uid is unique, so at most one row matches.
                matched_user = await uow.users.find_by_firebase_uid_including_deleted(
                    command.firebase_uid
                )
                existing_user = (
                    matched_user
                    if matched_user is not None and matched_user.is_active
                    else None
                )

                # An email is not proof that a new Firebase UID owns an existing
                # account.  Never rebind the account during an ordinary sync.
                if not existing_user and command.email:
                    email_owner = await uow.users.find_by_email(command.email)
                    if email_owner:
                        raise FirebaseIdentityConflictError(
                            "Firebase UID does not own the existing account"
                        )

                created = False
                updated = False
                user = None

                if existing_user:
                    # Update existing user
                    updated = self._update_existing_user(existing_user, command)
                    user = await uow.users.save(existing_user)
                    logger.info("Updated existing user")
                else:
                    if matched_user is not None and not matched_user.is_active:
                        logger.info(
                            f"Detected re-registration of deleted user (old_id={matched_user.id}). "
                            f"Creating fresh account with new ID."
                        )
                        # Note: We do NOT restore the old user; we create a brand new one
                        # The old record stays anonymized for audit trail

                    # Create new user (works for both new users and re-registrations)
                    user = self._create_new_user(command, uow)
                    language_code = self._supported_language_code(command.language_code)
                    if language_code:
                        user.language_code = language_code
                    user = await uow.users.save(user)
                    created = True
                    logger.info("Created new user")

                    # Create default notification preferences
                    await self._create_default_notification_preferences(user.id, uow)

                # Commit transaction
                await uow.commit()

                # Get subscription info from UoW
                subscription_info = None
                active_subscription = await uow.subscriptions.find_active_by_user_id(
                    str(user.id)
                )
                has_subscription = active_subscription is not None

                if active_subscription:
                    subscription_info = {
                        "product_id": active_subscription.product_id,
                        "status": active_subscription.status,
                        "expires_at": (
                            active_subscription.expires_at.isoformat()
                            if active_subscription.expires_at
                            else None
                        ),
                        "platform": active_subscription.platform,
                        "is_monthly": (
                            active_subscription.product_id.endswith("_monthly")
                            if active_subscription.product_id
                            else False
                        ),
                        "is_yearly": (
                            active_subscription.product_id.endswith("_yearly")
                            if active_subscription.product_id
                            else False
                        ),
                    }

                # Prepare response
                return {
                    "user": {
                        "id": user.id,
                        "firebase_uid": user.firebase_uid,
                        "email": user.email,
                        "username": user.username,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "phone_number": user.phone_number,
                        "display_name": user.display_name,
                        "photo_url": user.photo_url,
                        "provider": user.provider,
                        "is_active": user.is_active,
                        "onboarding_completed": user.onboarding_completed,
                        "last_accessed": user.last_accessed,
                        "created_at": user.created_at,
                        "updated_at": user.updated_at,
                        "has_subscription": has_subscription,
                        "subscription": subscription_info,
                    },
                    "created": created,
                    "updated": updated,
                    "message": (
                        "User created successfully"
                        if created
                        else (
                            "User updated successfully"
                            if updated
                            else "User data up to date"
                        )
                    ),
                }

            except Exception:
                await uow.rollback()
                raise

    @staticmethod
    def _supported_language_code(language_code: str | None) -> str | None:
        """Return a supported ISO code, or None when sync should keep the default."""
        if not language_code:
            return None
        from src.app.commands.user.update_language_command import (
            SUPPORTED_LANGUAGES,
        )

        lang = language_code.lower().strip()
        if lang in SUPPORTED_LANGUAGES:
            return lang
        return None

    def _create_new_user(
        self, command: SyncUserCommand, uow: AsyncUnitOfWorkPort
    ) -> UserDomainModel:
        """Create a new user domain model from Firebase data."""
        # Generate username if not provided
        username = command.username or self._generate_username(
            command.email, command.display_name
        )

        # Ensure username is unique (this logic requires repo support, simulating for now or skipping check)
        # Real impl would check uow.users.find_by_username(username)
        # username = self._ensure_unique_username(username, uow)

        # Extract names if not provided
        first_name, last_name = self._extract_names(
            command.display_name, command.first_name, command.last_name
        )

        # Create new user domain model
        return UserDomainModel(
            firebase_uid=command.firebase_uid,
            email=command.email,
            username=username,
            password_hash="",  # No password for Firebase users
            first_name=first_name,
            last_name=last_name,
            phone_number=command.phone_number,
            display_name=command.display_name,
            photo_url=command.photo_url,
            provider=AuthProvider.from_string(command.provider),
            is_active=True,
            onboarding_completed=False,
        )

    def _update_existing_user(
        self, user: UserDomainModel, command: SyncUserCommand
    ) -> bool:
        """Update existing user with new Firebase data."""
        updated = False
        provider = AuthProvider.from_string(command.provider)

        if user.provider != provider:
            user.provider = provider
            updated = True

        if user.email != command.email:
            user.email = command.email
            updated = True

        if user.phone_number != command.phone_number:
            user.phone_number = command.phone_number
            updated = True

        if user.display_name != command.display_name:
            user.display_name = command.display_name
            updated = True

        if user.photo_url != command.photo_url:
            user.photo_url = command.photo_url
            updated = True

        # Provider update logic?

        # Always update last_accessed
        user.last_accessed = utc_now()
        # updated = True # Logic says always updated if accessed? Maybe separation is better.

        return updated

    def _generate_username(self, email: str, display_name: str | None = None) -> str:
        """Generate a username from email or display name."""
        if display_name:
            username = re.sub(r"[^a-zA-Z0-9]", "", display_name.lower())
        else:
            username = email.split("@")[0]
            username = re.sub(r"[^a-zA-Z0-9]", "", username.lower())

        if len(username) < 3:
            username = f"user{username}"

        return username[:20]

    def _extract_names(
        self,
        display_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ):
        """Extract first and last names from display name or provided names."""
        if first_name and last_name:
            return first_name, last_name

        if display_name:
            name_parts = display_name.strip().split()
            if len(name_parts) >= 2:
                return name_parts[0], " ".join(name_parts[1:])
            elif len(name_parts) == 1:
                return name_parts[0], None

        return first_name, last_name

    async def _create_default_notification_preferences(self, user_id, uow):
        """Create default notification preferences. Non-fatal: logs and skips on error."""
        if not user_id:
            logger.warning("Cannot create notification preferences: user_id is None")
            return

        user_id_str = str(user_id)

        # Validate UUID format before constructing domain model (NUTREE-44)
        try:
            import uuid

            uuid.UUID(user_id_str)
        except ValueError:
            logger.warning(
                "Skipping notification prefs: invalid UUID value (len=%d)",
                len(user_id_str),
            )
            return

        try:
            default_prefs = NotificationPreferences.create_default(user_id_str)
            await uow.notifications.save_notification_preferences(default_prefs)
            logger.info(f"Added default notification preferences for user {user_id}")
        except Exception as e:
            # Non-fatal: notification prefs failure should not crash user sync
            logger.error(
                f"Failed to create notification preferences for {user_id}: {e}"
            )

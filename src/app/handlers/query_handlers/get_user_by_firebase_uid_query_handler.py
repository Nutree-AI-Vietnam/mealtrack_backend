"""
GetUserByFirebaseUidQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
import os
from collections.abc import Callable
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.user.get_user_by_firebase_uid_query import (
    GetUserByFirebaseUidQuery,
)
from src.app.services.subscription_access_fallback import (
    record_legacy_access_fallback,
)
from src.app.services.subscription_access_flags import (
    database_subscription_access_enabled,
)
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetUserByFirebaseUidQuery)
class GetUserByFirebaseUidQueryHandler(
    EventHandler[GetUserByFirebaseUidQuery, dict[str, Any]]
):
    """Handler for getting user by Firebase UID."""

    def __init__(self, uow_factory: Callable[[], AsyncUnitOfWorkPort] | None = None):
        self.uow_factory = uow_factory or AsyncUnitOfWork

    async def handle(self, query: GetUserByFirebaseUidQuery) -> dict[str, Any]:
        """Get user by Firebase UID."""
        async with self.uow_factory() as uow:
            # Get user by firebase_uid
            user = await uow.users.find_by_firebase_uid(query.firebase_uid)

            if not user:
                raise ResourceNotFoundException(
                    f"User with Firebase UID {query.firebase_uid} not found"
                )

            has_subscription_value = await self._has_subscription(uow, user)

            return {
                "id": user.id,
                "firebase_uid": user.firebase_uid,
                "email": user.email,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone_number": user.phone_number,
                "display_name": user.display_name,
                "photo_url": user.photo_url,
                "avatar": user.avatar.to_public_dict(),
                "provider": user.provider,
                "is_active": user.is_active,
                "onboarding_completed": user.onboarding_completed,
                "last_accessed": user.last_accessed,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
                # Required by UserProfileResponse
                "has_subscription": has_subscription_value,
            }

    @staticmethod
    async def _has_subscription(uow: AsyncUnitOfWorkPort, user: Any) -> bool:
        """Informational has_subscription; a profile fetch never fails on it.

        With the database policy on, stored facts decide. When they cannot
        (an unreconciled row and no other grant), the legacy decision below
        answers and the fallback is logged.
        """
        if database_subscription_access_enabled():
            subscriptions = await uow.subscriptions.find_by_user_id(str(user.id))
            try:
                return evaluate_subscription_access(subscriptions).has_access
            except SubscriptionAccessUnavailable:
                record_legacy_access_fallback(source="users_profile", user_id=user.id)
        # Legacy decision, unchanged from before the database policy.
        if os.getenv("ENVIRONMENT") == "development":
            return True
        try:
            return bool(user.has_active_subscription())
        except Exception:
            # Safe fallback if subscription lookup fails
            return False

"""
CompleteOnboardingCommandHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from typing import Any, Dict, Optional

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.user import CompleteOnboardingCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.ports.cache_port import CachePort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(CompleteOnboardingCommand)
class CompleteOnboardingCommandHandler(
    EventHandler[CompleteOnboardingCommand, Dict[str, Any]]
):
    """Handler for marking user onboarding as completed."""

    def __init__(
        self,
        cache_service: Optional[CachePort] = None,
        cache_invalidation: CacheInvalidationService | None = None,
    ):
        self.cache_invalidation = cache_invalidation or CacheInvalidationService(
            cache_service
        )

    async def handle(self, command: CompleteOnboardingCommand) -> Dict[str, Any]:
        """Mark user onboarding as completed if not already completed."""
        async with AsyncUnitOfWork() as uow:
            # Find user by firebase_uid
            user = await uow.users.find_by_firebase_uid(command.firebase_uid)

            if not user:
                raise ResourceNotFoundException(
                    f"User with Firebase UID {command.firebase_uid} not found"
                )

            # Check if onboarding is already completed
            if user.onboarding_completed:
                return {
                    "firebase_uid": command.firebase_uid,
                    "onboarding_completed": True,
                    "updated": False,
                    "message": "Onboarding already completed",
                }

            # Set onboarding as completed
            user.onboarding_completed = True
            user.last_accessed = utc_now()

            await uow.users.save(user)
            if self.cache_invalidation and getattr(uow, "outbox", None) is not None:
                await self.cache_invalidation.enqueue_profile_invalidation(
                    uow.outbox, str(user.id)
                )
            elif self.cache_invalidation:
                await self.cache_invalidation.after_profile_write(str(user.id))

        return {
            "firebase_uid": command.firebase_uid,
            "onboarding_completed": True,
            "updated": True,
            "message": "Onboarding marked as completed",
        }

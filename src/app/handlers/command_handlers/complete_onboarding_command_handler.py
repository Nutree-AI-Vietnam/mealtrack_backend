"""
CompleteOnboardingCommandHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.user import CompleteOnboardingCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_onboarding_completed_event import (
    UserOnboardingCompletedEvent,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(CompleteOnboardingCommand)
class CompleteOnboardingCommandHandler(
    EventHandler[CompleteOnboardingCommand, dict[str, Any]]
):
    """Handler for marking user onboarding as completed."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow = uow
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: CompleteOnboardingCommand) -> dict[str, Any]:
        """Mark user onboarding as completed if not already completed."""
        uow = self.uow or AsyncUnitOfWork()
        async with uow:
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

        event = UserOnboardingCompletedEvent(
            environment=self.environment,
            aggregate_id=str(user.id),
            data={"user_id": str(user.id)},
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())
        logger.info(
            "Published user onboarding completed integration event event_id=%s aggregate_id=%s",
            event.event_id,
            event.aggregate_id,
        )

        return {
            "firebase_uid": command.firebase_uid,
            "onboarding_completed": True,
            "updated": True,
            "message": "Onboarding marked as completed",
        }

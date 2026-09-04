"""Handler for updating custom macro targets."""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.user.update_custom_macros_command import UpdateCustomMacrosCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_custom_macros_updated_event import (
    UserCustomMacrosUpdatedEvent,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.infra.database.models.user.profile import UserProfile

logger = logging.getLogger(__name__)


@handles(UpdateCustomMacrosCommand)
class UpdateCustomMacrosCommandHandler(EventHandler[UpdateCustomMacrosCommand, None]):
    """Set or clear custom macro overrides on user profile."""

    def __init__(
        self,
        uow: AsyncUnitOfWorkPort | None = None,
        uow_factory: Any = None,
        event_publisher: IntegrationEventPublisherPort | None = None,
        environment: str = "development",
    ):
        self.uow_factory: Any = uow_factory or (lambda: uow)
        self.event_publisher = event_publisher
        self.environment = environment

    async def handle(self, command: UpdateCustomMacrosCommand) -> None:
        async with self.uow_factory() as uow:
            from sqlalchemy import select

            result = await uow.session.execute(
                select(UserProfile).where(
                    UserProfile.user_id == command.user_id,
                    UserProfile.is_current.is_(True),
                )
            )
            profile = result.scalars().first()

            if not profile:
                raise ResourceNotFoundException(
                    f"Current profile for user {command.user_id} not found"
                )

            # Validate all-or-nothing: all null (reset) or all non-null (set)
            values = [command.protein_g, command.carbs_g, command.fat_g]
            non_null_count = sum(1 for v in values if v is not None)
            if non_null_count not in (0, 3):
                raise ValidationException(
                    "Must set all three macros (protein, carbs, fat) or none to reset"
                )

            existing_values = (
                profile.custom_protein_g,
                profile.custom_carbs_g,
                profile.custom_fat_g,
            )
            profile.custom_protein_g = command.protein_g
            profile.custom_carbs_g = command.carbs_g
            profile.custom_fat_g = command.fat_g
            if existing_values != tuple(values):
                profile.profile_target_revision = (
                    profile.profile_target_revision or 1
                ) + 1

            action = "cleared" if non_null_count == 0 else "set"
            logger.info(f"Custom macros {action} for user {command.user_id}")

        event = UserCustomMacrosUpdatedEvent(
            environment=self.environment,
            aggregate_id=str(command.user_id),
            data={"user_id": str(command.user_id)},
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())
        logger.info(
            "Published custom macros updated integration event event_id=%s aggregate_id=%s",
            event.event_id,
            event.aggregate_id,
        )

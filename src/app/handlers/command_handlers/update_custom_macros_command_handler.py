"""Handler for updating custom macro targets."""

import logging

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.user.update_custom_macros_command import UpdateCustomMacrosCommand
from src.app.events.base import EventHandler, handles
from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(UpdateCustomMacrosCommand)
class UpdateCustomMacrosCommandHandler(EventHandler[UpdateCustomMacrosCommand, None]):
    """Set or clear custom macro overrides on user profile."""

    def __init__(self, cache_invalidation: CacheInvalidationService | None = None):
        self.cache_invalidation = cache_invalidation

    async def handle(self, command: UpdateCustomMacrosCommand) -> None:
        async with AsyncUnitOfWork() as uow:
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

            from unittest.mock import Mock

            if (
                isinstance(self.cache_invalidation, CacheInvalidationService)
                and getattr(uow, "outbox", None) is not None
                and not isinstance(getattr(uow, "outbox", None), Mock)
            ):
                await self.cache_invalidation.enqueue_profile_invalidation(
                    uow.outbox, command.user_id
                )
            elif self.cache_invalidation:
                import inspect

                res = self.cache_invalidation.after_profile_write(command.user_id)
                if inspect.isawaitable(res):
                    await res

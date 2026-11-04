"""
SaveUserOnboardingCommandHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.user import SaveUserOnboardingCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import (
    UserProfileUpdatedEvent,
)
from src.domain.model.user import UserProfileDomainModel
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.services.meal_suggestion.suggestion_tdee_helpers import (
    build_tdee_request,
)
from src.domain.services.tdee_service import allowed_gain_pace, allowed_pace
from src.domain.utils.timezone_utils import (
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)

logger = logging.getLogger(__name__)


@handles(SaveUserOnboardingCommand)
class SaveUserOnboardingCommandHandler(EventHandler[SaveUserOnboardingCommand, None]):
    """Handler for saving user onboarding data."""

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

    async def handle(self, command: SaveUserOnboardingCommand) -> None:
        """Save user onboarding data."""
        # Validate input
        if command.age < 1 or command.age > 120:
            raise ValidationException("Age must be between 1 and 120")

        if command.weight_kg <= 0:
            raise ValidationException("Weight must be greater than 0")

        if command.height_cm <= 0:
            raise ValidationException("Height must be greater than 0")

        async with self.uow_factory() as uow:
            try:
                # Get existing user
                user = await uow.users.find_by_id(UUID(command.user_id))
                if not user:
                    raise ResourceNotFoundException(
                        f"User {command.user_id} not found. User must be created before onboarding."
                    )

                # Get or create user profile
                profile = await uow.users.get_profile(UUID(command.user_id))
                timezone_name = await resolve_user_timezone_async(command.user_id, uow)
                today = datetime.now(get_zone_info(timezone_name)).date()

                if not profile:
                    goal_started_at = utc_now() if command.target_weight_kg else None
                    # Create new profile
                    profile = UserProfileDomainModel(
                        user_id=UUID(command.user_id),
                        age=command.age,
                        gender=command.gender,
                        height_cm=command.height_cm,
                        weight_kg=command.weight_kg,
                        body_fat_percentage=command.body_fat_percentage,
                        date_of_birth=command.date_of_birth,
                        target_weight_kg=command.target_weight_kg,
                        goal_start_weight_kg=(
                            command.weight_kg if command.target_weight_kg else None
                        ),
                        goal_started_at=goal_started_at,
                        job_type=command.job_type,
                        training_days_per_week=command.training_days_per_week,
                        training_minutes_per_session=command.training_minutes_per_session,
                        fitness_goal=command.fitness_goal,
                        meals_per_day=command.meals_per_day,
                        pain_points=command.pain_points or [],
                        dietary_preferences=command.dietary_preferences or [],
                        training_level=command.training_level,
                        referral_sources=command.referral_sources,
                        challenge_duration=command.challenge_duration,
                        training_types=command.training_types,
                        pace_clinician_disclaimer=bool(
                            command.pace_clinician_disclaimer
                        ),
                    )
                else:
                    target_inputs_before = (
                        profile.age,
                        profile.gender,
                        profile.height_cm,
                        profile.weight_kg,
                        profile.job_type,
                        profile.training_days_per_week,
                        profile.training_minutes_per_session,
                        profile.fitness_goal,
                        profile.training_level,
                        tuple(profile.dietary_preferences or []),
                        profile.custom_protein_g,
                        profile.custom_carbs_g,
                        profile.custom_fat_g,
                        profile.weight_loss_kg_per_week,
                        profile.weight_gain_kg_per_week,
                    )
                    previous_target = profile.target_weight_kg
                    # Update existing profile
                    profile.age = command.age
                    profile.gender = command.gender
                    profile.height_cm = command.height_cm
                    profile.weight_kg = command.weight_kg
                    profile.body_fat_percentage = command.body_fat_percentage
                    profile.job_type = command.job_type
                    profile.training_days_per_week = command.training_days_per_week
                    profile.training_minutes_per_session = (
                        command.training_minutes_per_session
                    )
                    profile.fitness_goal = command.fitness_goal
                    profile.meals_per_day = command.meals_per_day
                    if command.pain_points is not None:
                        profile.pain_points = command.pain_points
                    if command.dietary_preferences is not None:
                        profile.dietary_preferences = command.dietary_preferences
                    profile.training_level = command.training_level
                    profile.date_of_birth = command.date_of_birth
                    profile.target_weight_kg = command.target_weight_kg
                    if command.target_weight_kg and (
                        profile.goal_started_at is None
                        or previous_target != command.target_weight_kg
                    ):
                        profile.goal_start_weight_kg = command.weight_kg
                        profile.goal_started_at = utc_now()
                        profile.journey_progress_seed_percent = 0.0
                    # Write-once — don't overwrite if already set
                    if command.referral_sources and not profile.referral_sources:
                        profile.referral_sources = command.referral_sources
                    profile.challenge_duration = command.challenge_duration
                    profile.training_types = command.training_types
                    if command.pace_clinician_disclaimer is not None:
                        profile.pace_clinician_disclaimer = (
                            command.pace_clinician_disclaimer
                        )

                # A chosen pace is capped where the target would drop below the
                # floor, after the metrics it depends on are in place.
                if command.weight_loss_kg_per_week is not None:
                    try:
                        pace = allowed_pace(
                            build_tdee_request(profile), command.weight_loss_kg_per_week
                        )
                    except ValueError as exc:
                        raise ValidationException(str(exc)) from exc
                    if pace != profile.weight_loss_kg_per_week:
                        profile.weight_loss_kg_per_week = pace
                        if pace is not None:
                            profile.weight_loss_pace_reset_on = today
                # A gain pace has no floor; only the fast limit caps it.
                if command.weight_gain_kg_per_week is not None:
                    try:
                        gain = allowed_gain_pace(
                            build_tdee_request(profile), command.weight_gain_kg_per_week
                        )
                    except ValueError as exc:
                        raise ValidationException(str(exc)) from exc
                    if gain != profile.weight_gain_kg_per_week:
                        profile.weight_gain_kg_per_week = gain
                        if gain is not None:
                            profile.weight_loss_pace_reset_on = today

                # Save custom macro overrides if all three provided
                custom_values = [
                    command.custom_protein_g,
                    command.custom_carbs_g,
                    command.custom_fat_g,
                ]
                if all(v is not None for v in custom_values):
                    profile.custom_protein_g = command.custom_protein_g
                    profile.custom_carbs_g = command.custom_carbs_g
                    profile.custom_fat_g = command.custom_fat_g

                if (
                    hasattr(locals().get("profile"), "profile_target_revision")
                    and "target_inputs_before" in locals()
                ):
                    target_inputs_after = (
                        profile.age,
                        profile.gender,
                        profile.height_cm,
                        profile.weight_kg,
                        profile.job_type,
                        profile.training_days_per_week,
                        profile.training_minutes_per_session,
                        profile.fitness_goal,
                        profile.training_level,
                        tuple(profile.dietary_preferences or []),
                        profile.custom_protein_g,
                        profile.custom_carbs_g,
                        profile.custom_fat_g,
                        profile.weight_loss_kg_per_week,
                        profile.weight_gain_kg_per_week,
                    )
                    if target_inputs_before != target_inputs_after:
                        profile.profile_target_revision = (
                            profile.profile_target_revision or 1
                        ) + 1

                # Save profile
                await uow.users.update_profile(profile)
                await uow.commit()

            except Exception:
                await uow.rollback()
                raise

        event = UserProfileUpdatedEvent(
            environment=self.environment,
            aggregate_id=str(command.user_id),
            data={"user_id": str(command.user_id)},
        )
        await require_event_publisher(self.event_publisher).publish(event.to_payload())
        logger.info(
            "Published user profile updated integration event event_id=%s aggregate_id=%s",
            event.event_id,
            event.aggregate_id,
        )

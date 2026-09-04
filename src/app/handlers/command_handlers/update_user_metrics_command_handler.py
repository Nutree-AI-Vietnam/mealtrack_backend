"""
Command handler for updating user metrics.
"""

import logging
from typing import Any

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import (
    UserProfileUpdatedEvent,
)
from src.domain.model.common.enums import FitnessGoal, JobType, TrainingLevel
from src.domain.model.user.body_fat_visual import remap_visual_profile_selection
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.services.training_policy import normalize_training_pair
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

_VALID_JOB_TYPES = {e.value for e in JobType}
_VALID_FITNESS_GOALS = {e.value for e in FitnessGoal}
_VALID_TRAINING_LEVELS = {e.value for e in TrainingLevel}
_VALID_BIOLOGICAL_SEXES = {"male", "female"}


def _has_metric_update(command: UpdateUserMetricsCommand) -> bool:
    return (
        any(
            value is not None
            for value in [
                command.weight_kg,
                command.height_cm,
                command.age,
                command.biological_sex,
                command.job_type,
                command.training_days_per_week,
                command.training_minutes_per_session,
                command.body_fat_percent,
                command.fitness_goal,
                command.training_level,
                command.target_weight_kg,
                command.goal_start_weight_kg,
                command.goal_started_at,
                command.daily_water_goal_ml,
            ]
        )
        or command.body_fat_percent_provided
        or command.reset_water_goal
    )


@handles(UpdateUserMetricsCommand)
class UpdateUserMetricsCommandHandler(EventHandler[UpdateUserMetricsCommand, None]):
    """Handle updating user metrics (weight, job type, training, body fat)."""

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

    async def handle(self, command: UpdateUserMetricsCommand) -> None:
        # Validate at least one field is provided
        if not _has_metric_update(command):
            raise ValidationException("At least one metric must be provided")

        async with self.uow_factory() as uow:
            profile = await uow.users.get_profile(command.user_id)

            if not profile:
                raise ResourceNotFoundException(
                    f"User {command.user_id} not found. Profile required to update metrics."
                )

            target_inputs_before = (
                profile.age,
                profile.height_cm,
                profile.weight_kg,
                profile.gender,
                profile.job_type,
                profile.training_days_per_week,
                profile.training_minutes_per_session,
                profile.fitness_goal,
                profile.training_level,
            )
            sex_changed = False

            # Update provided fields only
            if command.age is not None:
                if command.age < 12 or command.age > 120:
                    raise ValidationException("Age must be between 12 and 120")
                profile.age = command.age

            if command.height_cm is not None:
                if command.height_cm < 100 or command.height_cm > 272:
                    raise ValidationException("Height must be between 100 and 272 cm")
                profile.height_cm = command.height_cm

            if command.weight_kg is not None:
                if command.weight_kg <= 0:
                    raise ValidationException("Weight must be greater than 0")
                profile.weight_kg = command.weight_kg

            if command.biological_sex is not None:
                if command.biological_sex not in _VALID_BIOLOGICAL_SEXES:
                    raise ValidationException("Biological sex must be male or female")
                sex_changed = profile.gender != command.biological_sex
                profile.gender = command.biological_sex

            if command.job_type is not None:
                if command.job_type not in _VALID_JOB_TYPES:
                    raise ValidationException(
                        f"Job type must be one of: {', '.join(sorted(_VALID_JOB_TYPES))}"
                    )
                profile.job_type = command.job_type

            if (
                command.training_days_per_week is not None
                or command.training_minutes_per_session is not None
            ):
                effective_days = (
                    command.training_days_per_week
                    if command.training_days_per_week is not None
                    else profile.training_days_per_week
                )
                effective_minutes = (
                    0
                    if command.training_days_per_week == 0
                    else (
                        command.training_minutes_per_session
                        if command.training_minutes_per_session is not None
                        else profile.training_minutes_per_session
                    )
                )
                try:
                    effective_days, effective_minutes = normalize_training_pair(
                        effective_days, effective_minutes, allow_legacy=True
                    )
                except ValueError as exc:
                    raise ValidationException(str(exc)) from exc
                profile.training_days_per_week = effective_days
                profile.training_minutes_per_session = effective_minutes

            if (
                command.body_fat_percent is not None
                or command.body_fat_percent_provided
            ):
                if command.body_fat_percent is not None and (
                    command.body_fat_percent < 0 or command.body_fat_percent > 70
                ):
                    raise ValidationException(
                        "Body fat percentage must be between 0 and 70"
                    )
                profile.body_fat_percentage = command.body_fat_percent

            # Handle fitness goal update with logging
            if command.fitness_goal is not None:
                if command.fitness_goal not in _VALID_FITNESS_GOALS:
                    raise ValidationException(
                        f"Fitness goal must be one of: {', '.join(sorted(_VALID_FITNESS_GOALS))}"
                    )

                # Log goal changes for analytics
                if profile.fitness_goal != command.fitness_goal:
                    logger.info(
                        "Fitness goal changed for user %s: %s -> %s",
                        command.user_id,
                        profile.fitness_goal,
                        command.fitness_goal,
                    )
                    profile.fitness_goal = command.fitness_goal

            # Handle training level update
            if command.training_level is not None:
                if command.training_level not in _VALID_TRAINING_LEVELS:
                    raise ValidationException(
                        f"Training level must be one of: {sorted(_VALID_TRAINING_LEVELS)}"
                    )
                profile.training_level = command.training_level

            # Handle target weight update
            target_weight_changed = False
            should_auto_start_goal = False
            if command.target_weight_kg is not None:
                if command.target_weight_kg <= 0:
                    raise ValidationException("Target weight must be greater than 0")
                target_weight_changed = (
                    profile.target_weight_kg != command.target_weight_kg
                )
                should_auto_start_goal = (
                    target_weight_changed or profile.goal_started_at is None
                )
                logger.info(
                    f"Updating target_weight_kg for user {command.user_id}: "
                    f"{profile.target_weight_kg} -> {command.target_weight_kg}"
                )
                profile.target_weight_kg = command.target_weight_kg

            # Handle goal start fields (for progress tracking reset)
            if command.goal_start_weight_kg is not None:
                if command.goal_start_weight_kg <= 0:
                    raise ValidationException(
                        "Goal start weight must be greater than 0"
                    )
                logger.info(
                    f"Updating goal_start_weight_kg for user {command.user_id}: "
                    f"{profile.goal_start_weight_kg} -> {command.goal_start_weight_kg}"
                )
                profile.goal_start_weight_kg = command.goal_start_weight_kg
            elif should_auto_start_goal:
                profile.goal_start_weight_kg = profile.weight_kg

            if command.goal_started_at is not None:
                logger.info(
                    f"Updating goal_started_at for user {command.user_id}: "
                    f"{profile.goal_started_at} -> {command.goal_started_at}"
                )
                profile.goal_started_at = command.goal_started_at
            elif should_auto_start_goal:
                profile.goal_started_at = utc_now()

            if (
                should_auto_start_goal
                or command.goal_start_weight_kg is not None
                or command.goal_started_at is not None
            ):
                profile.journey_progress_seed_percent = 0.0

            if command.reset_water_goal:
                profile.daily_water_goal_ml = None
            elif command.daily_water_goal_ml is not None:
                if command.daily_water_goal_ml <= 0:
                    raise ValidationException("Daily water goal must be greater than 0")
                profile.daily_water_goal_ml = command.daily_water_goal_ml

            # Ensure this profile is marked as current
            profile.is_current = True

            target_inputs_after = (
                profile.age,
                profile.height_cm,
                profile.weight_kg,
                profile.gender,
                profile.job_type,
                profile.training_days_per_week,
                profile.training_minutes_per_session,
                profile.fitness_goal,
                profile.training_level,
            )
            if target_inputs_after != target_inputs_before:
                profile.profile_target_revision = (
                    profile.profile_target_revision or 1
                ) + 1

            if sex_changed:
                history = await uow.body_fat_visual_profiles.find_history_by_user(
                    command.user_id
                )
                if history:
                    await uow.body_fat_visual_profiles.append(
                        remap_visual_profile_selection(
                            history[-1], target_sex=profile.gender
                        )
                    )

            await uow.users.update_profile(profile)

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

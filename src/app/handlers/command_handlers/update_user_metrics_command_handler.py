"""
Command handler for updating user metrics.
"""

import logging
from datetime import date, datetime
from typing import Any

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.events.base import EventHandler, handles
from src.app.events.user.user_profile_updated_event import (
    UserProfileUpdatedEvent,
)
from src.domain.model.common.enums import FitnessGoal, JobType, TrainingLevel
from src.domain.model.user.body_fat_visual import remap_visual_profile_selection
from src.domain.model.user.core_user import age_on
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
    require_event_publisher,
)
from src.domain.services.meal_suggestion.suggestion_tdee_helpers import (
    build_tdee_request,
)
from src.domain.services.tdee_service import HEALTH_CONDITIONS, allowed_pace
from src.domain.services.training_policy import normalize_training_pair
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    resolve_allergen_terms,
)
from src.domain.utils.timezone_utils import (
    get_zone_info,
    resolve_user_timezone_async,
    utc_now,
)

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
                command.date_of_birth,
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
        or command.weight_loss_kg_per_week is not None
        or command.dietary_preferences is not None
        or command.health_conditions is not None
        or command.allergies is not None
    )


def _checked_conditions(values: list[str]) -> list[str]:
    """Only conditions the targets actually act on.

    An unrecognised string would be stored and then silently ignored, which
    reads to the user as "I told you" while nothing changed.
    """
    cleaned = []
    for value in _clean_diets(values):
        condition = value.strip().lower().replace(" ", "_").replace("-", "_")
        if condition not in HEALTH_CONDITIONS:
            raise ValidationException(
                f"Unknown health condition: {value}",
                error_code="HEALTH_CONDITION_UNSUPPORTED",
            )
        if condition not in cleaned:
            cleaned.append(condition)
    return cleaned


async def _checked_allergies(uow, values: list[str]) -> list[str]:
    """Keep the user's own words, but only terms the recipe data can check.

    Saving a term the planner cannot resolve would leave the user believing
    an allergy is handled when no recipe is ever excluded for it.
    """
    cleaned = _clean_diets(values)
    if not cleaned:
        return []
    lister = getattr(uow.catalog_recipes, "list_allergen_codes", None)
    known = tuple(await lister()) if callable(lister) else ()
    _, unresolved = resolve_allergen_terms(cleaned, known)
    if unresolved:
        raise ValidationException(
            "These allergies can't be checked against the recipe data yet: "
            + ", ".join(unresolved),
            error_code="ALLERGY_UNRESOLVED",
        )
    return cleaned


def _clean_diets(values: list[str]) -> list[str]:
    """Trimmed diets in the order given; blanks and repeats in any case dropped."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        diet = str(value).strip()
        if diet and diet.casefold() not in seen:
            seen.add(diet.casefold())
            cleaned.append(diet)
    return cleaned


def _value_set(values: list[str] | None) -> frozenset[str]:
    """Values as a set, so reordering or recasing them is not a target change."""
    return frozenset(value.casefold() for value in _clean_diets(list(values or ())))


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

            # The date of birth sits with the target inputs because the age
            # comes from it, and GET metrics' cache is fenced by this revision.
            target_inputs_before = (
                profile.age,
                profile.date_of_birth,
                profile.height_cm,
                profile.weight_kg,
                profile.gender,
                profile.job_type,
                profile.training_days_per_week,
                profile.training_minutes_per_session,
                profile.fitness_goal,
                profile.training_level,
                profile.weight_loss_kg_per_week,
                _value_set(profile.dietary_preferences),
                # Conditions change the safe target; allergies only show in GET
                # metrics, whose cache this revision also fences.
                _value_set(profile.health_conditions),
                _value_set(profile.allergies),
            )
            journey_inputs_before = (
                profile.target_weight_kg,
                profile.goal_start_weight_kg,
                profile.goal_started_at,
            )
            sex_changed = False

            # Update provided fields only. A date of birth sets the age and
            # wins over an age sent with it.
            if command.date_of_birth is not None:
                today = date.today()
                age = age_on(command.date_of_birth, today)
                if command.date_of_birth > today or age < 12 or age > 120:
                    raise ValidationException(
                        "Date of birth must be past and give an age between 12 and 120"
                    )
                profile.date_of_birth = command.date_of_birth
                profile.age = age
            elif command.age is not None:
                if command.age < 12 or command.age > 120:
                    raise ValidationException("Age must be between 12 and 120")
                # Apps without the date picker send the stored age with every
                # My Plan save, and it lags the date after a birthday. Only an
                # age they changed, to one the date does not give, means the
                # stored date of birth is wrong now.
                if (
                    profile.date_of_birth is not None
                    and command.age != profile.age
                    and command.age != age_on(profile.date_of_birth, date.today())
                ):
                    profile.date_of_birth = None
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

            if command.weight_loss_kg_per_week is not None:
                # Capped where the target would drop below the floor, using the
                # metrics this same update just set.
                try:
                    pace = allowed_pace(
                        build_tdee_request(profile), command.weight_loss_kg_per_week
                    )
                except ValueError as exc:
                    raise ValidationException(str(exc)) from exc
                if profile.weight_loss_kg_per_week != pace:
                    timezone_name = await resolve_user_timezone_async(
                        command.user_id, uow
                    )
                    profile.weight_loss_kg_per_week = pace
                    profile.weight_loss_pace_reset_on = datetime.now(
                        get_zone_info(timezone_name)
                    ).date()

            if command.dietary_preferences is not None:
                profile.dietary_preferences = _clean_diets(command.dietary_preferences)

            if command.health_conditions is not None:
                profile.health_conditions = _checked_conditions(
                    command.health_conditions
                )

            if command.allergies is not None:
                profile.allergies = await _checked_allergies(uow, command.allergies)

            # Ensure this profile is marked as current
            profile.is_current = True

            target_inputs_after = (
                profile.age,
                profile.date_of_birth,
                profile.height_cm,
                profile.weight_kg,
                profile.gender,
                profile.job_type,
                profile.training_days_per_week,
                profile.training_minutes_per_session,
                profile.fitness_goal,
                profile.training_level,
                profile.weight_loss_kg_per_week,
                _value_set(profile.dietary_preferences),
                # Conditions change the safe target; allergies only show in GET
                # metrics, whose cache this revision also fences.
                _value_set(profile.health_conditions),
                _value_set(profile.allergies),
            )
            journey_inputs_after = (
                profile.target_weight_kg,
                profile.goal_start_weight_kg,
                profile.goal_started_at,
            )
            if (
                target_inputs_after != target_inputs_before
                or journey_inputs_after != journey_inputs_before
            ):
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

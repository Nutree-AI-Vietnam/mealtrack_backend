"""
TDEE calculation helpers for SuggestionOrchestrationService.
Builds TdeeRequest from user profile and fetches adjusted daily target from weekly budget.
"""
import logging
from typing import Any

from src.domain.mappers.activity_goal_mapper import ActivityGoalMapper
from src.domain.model.user import (
    MacroPreset,
    MacroTargets,
    Sex,
    TdeeRequest,
    UnitSystem,
)
from src.domain.services.tdee_service import TdeeCalculationService
from src.domain.services.weekly_budget_service import WeeklyBudgetService
from src.domain.utils.timezone_utils import (
    get_user_monday_async,
    resolve_user_timezone_async,
    user_today,
)

logger = logging.getLogger(__name__)


def build_tdee_request(profile: Any) -> TdeeRequest:
    """Build a TdeeRequest from a user profile domain object."""
    gender = profile.gender or "male"
    sex = Sex.MALE if gender.lower() == "male" else Sex.FEMALE
    preferences = getattr(profile, "dietary_preferences", None)
    preferences = preferences if isinstance(preferences, (list, tuple, set)) else ()
    preset = (
        MacroPreset.KETO
        if any(value.casefold() == "keto" for value in preferences if isinstance(value, str))
        else MacroPreset.STANDARD
    )
    return TdeeRequest(
        age=profile.age,
        sex=sex,
        height=profile.height_cm,
        weight=profile.weight_kg,
        job_type=ActivityGoalMapper.map_job_type(profile.job_type),
        training_days_per_week=profile.training_days_per_week,
        training_minutes_per_session=profile.training_minutes_per_session,
        training_level=ActivityGoalMapper.map_training_level(profile.training_level),
        goal=ActivityGoalMapper.map_goal(profile.fitness_goal),
        body_fat_pct=profile.body_fat_percentage,
        unit_system=UnitSystem.METRIC,
        macro_preset=preset,
    )


def calculate_daily_tdee(tdee_service: TdeeCalculationService, profile: Any) -> float:
    """Calculate the authoritative raw daily target without fabricating a fallback."""
    # Legacy suggestion providers may supply only meal preferences. They are not
    # authoritative profile readers and retain the historical generic target.
    if not isinstance(getattr(profile, "age", None), (int, float)):
        return 2000.0
    custom_values = (
        getattr(profile, "custom_protein_g", None),
        getattr(profile, "custom_carbs_g", None),
        getattr(profile, "custom_fat_g", None),
    )
    if all(isinstance(value, (int, float)) for value in custom_values):
        return round(
            profile.custom_protein_g * 4 + profile.custom_carbs_g * 4 + profile.custom_fat_g * 9,
            1,
        )
    return tdee_service.calculate_tdee(build_tdee_request(profile)).macros.calories


async def get_adjusted_daily_target(
    tdee_service: TdeeCalculationService, user_id: str, profile: Any, uow: Any = None
) -> float:
    """Return adjusted daily calorie target using Skip & Redistribute.

    Delegates to WeeklyBudgetService.get_effective_adjusted_daily_async() which
    recalculates consumed from actual meals (not stale DB values).
    Falls back to raw TDEE if no budget exists or uow not provided.
    """
    try:
        request = build_tdee_request(profile)
        tdee_result = tdee_service.calculate_tdee(request)
        base_calories = tdee_result.macros.calories
        is_custom = all(
            isinstance(value, (int, float))
            for value in (
                getattr(profile, "custom_protein_g", None),
                getattr(profile, "custom_carbs_g", None),
                getattr(profile, "custom_fat_g", None),
            )
        )
        if is_custom:
            base_calories = calculate_daily_tdee(tdee_service, profile)
        bmr = tdee_result.bmr

        if uow is None:
            logger.info(f"No UoW provided for user {user_id}, using raw TDEE: {base_calories}")
            return base_calories

        user_tz = await resolve_user_timezone_async(user_id, uow)
        today = user_today(user_tz)
        week_start = await get_user_monday_async(today, user_id, uow)
        weekly_budget = await uow.weekly_budgets.find_by_user_and_week(user_id, week_start)

        if not weekly_budget:
            logger.info(f"No weekly budget for user {user_id}, using raw TDEE: {base_calories}")
            return base_calories
        if weekly_budget.target_revision != getattr(profile, "profile_target_revision", None):
            raise ValueError("Weekly target revision is stale")

        # Use async shared method: recalculates consumed, applies skip/redistribute
        auto_adjust = WeeklyBudgetService.auto_adjust_enabled(
            await uow.users.get_weekly_auto_adjust(user_id)
        )
        effective = await WeeklyBudgetService.get_effective_adjusted_daily_async(
            uow=uow, user_id=user_id,
            week_start=week_start, target_date=today,
            weekly_budget=weekly_budget,
            base_daily_cal=base_calories,
            base_daily_protein=tdee_result.macros.protein,
            base_daily_carbs=tdee_result.macros.carbs,
            base_daily_fat=tdee_result.macros.fat,
            bmr=bmr, user_timezone=user_tz,
            auto_adjust=auto_adjust,
        )
        logger.info(
            f"Adjusted daily target for user {user_id}: "
            f"{effective.adjusted.calories:.0f} kcal (base: {base_calories:.0f}, "
            f"bmr_floor: {effective.adjusted.bmr_floor_active})"
        )
        policy = TdeeCalculationService.apply_adjusted_macro_policy(
            effective.adjusted.calories,
            MacroTargets(
                calories=effective.adjusted.calories,
                protein=effective.adjusted.protein,
                carbs=effective.adjusted.carbs,
                fat=effective.adjusted.fat,
            ),
            request.macro_preset,
            is_custom,
        )
        return policy.calories

    except Exception as e:
        logger.warning("Failed to get adjusted daily target: %s", e)
        return calculate_daily_tdee(tdee_service, profile)

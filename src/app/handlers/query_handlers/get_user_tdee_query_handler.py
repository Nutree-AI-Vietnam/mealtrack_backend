"""
GetUserTdeeQueryHandler - Individual handler file.
Auto-extracted for better maintainability.
"""

import logging
from typing import Any

from sqlalchemy import select

from src.api.exceptions import ResourceNotFoundException
from src.app.events.base import EventHandler, handles
from src.app.queries.tdee import GetUserTdeeQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.constants import NutritionConstants, TDEEConstants
from src.domain.mappers.activity_goal_mapper import ActivityGoalMapper
from src.domain.model.user import MacroPreset, Sex, TdeeRequest, UnitSystem
from src.domain.ports.cache_port import CachePort
from src.domain.services.tdee_service import TdeeCalculationService
from src.infra.database.models.user.profile import UserProfile
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)


@handles(GetUserTdeeQuery)
class GetUserTdeeQueryHandler(EventHandler[GetUserTdeeQuery, dict[str, Any]]):
    """Handler for getting user's TDEE calculation."""

    def __init__(
        self,
        tdee_service: TdeeCalculationService = None,
        cache_service: CachePort | None = None,
    ):
        self.tdee_service = tdee_service or TdeeCalculationService()
        self.cache_service = cache_service

    async def handle(self, query: GetUserTdeeQuery) -> dict[str, Any]:
        cache_key, ttl = CacheKeys.user_tdee(query.user_id)
        current_revision = await self._current_profile_revision(query.user_id)
        if self.cache_service:
            cached = await self.cache_service.get_json(cache_key)
            if (
                cached is not None
                and cached.get("profile_target_revision") == current_revision
            ):
                return cached
        result = await self._compute_tdee(query)
        if self.cache_service:
            await self.cache_service.set_json(
                cache_key, result, ttl, revision_field="profile_target_revision"
            )
        return result

    async def _current_profile_revision(self, user_id: str) -> int:
        async with AsyncUnitOfWork() as uow:
            result = await uow.session.execute(
                select(UserProfile.profile_target_revision).where(
                    UserProfile.user_id == user_id, UserProfile.is_current.is_(True)
                )
            )
            revision = result.scalar_one_or_none()
        if revision is None:
            raise ResourceNotFoundException(
                f"Current profile for user {user_id} not found"
            )
        return revision

    async def _compute_tdee(self, query: GetUserTdeeQuery) -> dict[str, Any]:
        """Get user's TDEE calculation based on current profile."""
        async with AsyncUnitOfWork() as uow:
            # Get current user profile using the UnitOfWork session
            result = await uow.session.execute(
                select(UserProfile).where(
                    UserProfile.user_id == query.user_id,
                    UserProfile.is_current.is_(True),
                )
            )
            profile = result.scalars().first()

            if not profile:
                raise ResourceNotFoundException(
                    f"Current profile for user {query.user_id} not found"
                )

            macro_preset = (
                MacroPreset.KETO
                if any(
                    value.casefold() == "keto"
                    for value in (profile.dietary_preferences or [])
                )
                else MacroPreset.STANDARD
            )

            # Check for custom macro overrides first
            if profile.has_custom_macros:
                return self._build_custom_macros_response(query, profile, macro_preset)

            # Map profile data to TDEE request using centralized mapper
            sex = Sex.MALE if profile.gender.lower() == "male" else Sex.FEMALE

            # Map training level if profile has it
            training_level = None
            if profile.training_level:
                training_level = ActivityGoalMapper.map_training_level(
                    profile.training_level
                )

            tdee_request = TdeeRequest(
                age=profile.age,
                sex=sex,
                height=profile.height_cm,
                weight=profile.weight_kg,
                job_type=ActivityGoalMapper.map_job_type(profile.job_type),
                training_days_per_week=profile.training_days_per_week,
                training_minutes_per_session=profile.training_minutes_per_session,
                goal=ActivityGoalMapper.map_goal(profile.fitness_goal),
                body_fat_pct=profile.body_fat_percentage,
                unit_system=UnitSystem.METRIC,
                training_level=training_level,
                macro_preset=macro_preset,
            )

            # Calculate TDEE
            result = self.tdee_service.calculate_tdee(tdee_request)

            # Baseline excludes planned workouts; logged movement credits them.
            base_multiplier = TDEEConstants.JOB_TYPE_MULTIPLIERS.get(
                profile.job_type, 1.2
            )

            macros = self._canonical_macros(
                result.macros.protein,
                result.macros.carbs,
                result.macros.fat,
            )

            return {
                "user_id": query.user_id,
                "bmr": result.bmr,
                "tdee": result.tdee,
                "target_calories": macros["calories"],
                "activity_multiplier": round(base_multiplier, 3),
                "formula_used": result.formula_used,
                "is_custom": False,
                "macro_preset": result.macro_preset.value,
                "profile_target_revision": profile.profile_target_revision,
                "target_revision": profile.profile_target_revision,
                "macros": macros,
                "profile_data": {
                    "age": profile.age,
                    "gender": profile.gender,
                    "height_cm": profile.height_cm,
                    "weight_kg": profile.weight_kg,
                    "job_type": profile.job_type,
                    "training_days_per_week": profile.training_days_per_week,
                    "training_minutes_per_session": profile.training_minutes_per_session,
                    "fitness_goal": profile.fitness_goal,
                    "body_fat_percentage": profile.body_fat_percentage,
                },
            }

    def _build_custom_macros_response(
        self, query: GetUserTdeeQuery, profile, macro_preset: MacroPreset
    ) -> dict[str, Any]:
        """Build response using custom macro overrides, still calculating BMR/TDEE for reference."""
        macros = self._canonical_macros(
            profile.custom_protein_g,
            profile.custom_carbs_g,
            profile.custom_fat_g,
        )

        # Still calculate BMR/TDEE for reference display
        sex = Sex.MALE if profile.gender.lower() == "male" else Sex.FEMALE
        training_level = None
        if profile.training_level:
            training_level = ActivityGoalMapper.map_training_level(
                profile.training_level
            )

        tdee_request = TdeeRequest(
            age=profile.age,
            sex=sex,
            height=profile.height_cm,
            weight=profile.weight_kg,
            job_type=ActivityGoalMapper.map_job_type(profile.job_type),
            training_days_per_week=profile.training_days_per_week,
            training_minutes_per_session=profile.training_minutes_per_session,
            goal=ActivityGoalMapper.map_goal(profile.fitness_goal),
            body_fat_pct=profile.body_fat_percentage,
            unit_system=UnitSystem.METRIC,
            training_level=training_level,
            macro_preset=macro_preset,
        )
        result = self.tdee_service.calculate_tdee(tdee_request)

        base_multiplier = TDEEConstants.JOB_TYPE_MULTIPLIERS.get(profile.job_type, 1.2)

        return {
            "user_id": query.user_id,
            "bmr": result.bmr,
            "tdee": result.tdee,
            "target_calories": macros["calories"],
            "activity_multiplier": round(base_multiplier, 3),
            "formula_used": result.formula_used,
            "is_custom": True,
            "macro_preset": macro_preset.value,
            "profile_target_revision": profile.profile_target_revision,
            "target_revision": profile.profile_target_revision,
            "macros": macros,
            "profile_data": {
                "age": profile.age,
                "gender": profile.gender,
                "height_cm": profile.height_cm,
                "weight_kg": profile.weight_kg,
                "job_type": profile.job_type,
                "training_days_per_week": profile.training_days_per_week,
                "training_minutes_per_session": profile.training_minutes_per_session,
                "fitness_goal": profile.fitness_goal,
                "body_fat_percentage": profile.body_fat_percentage,
            },
        }

    @staticmethod
    def _canonical_macros(protein: float, carbs: float, fat: float) -> dict[str, float]:
        """Round macro grams first, then derive the canonical calorie target."""
        protein = round(protein, 1)
        carbs = round(carbs, 1)
        fat = round(fat, 1)
        return {
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
            "calories": round(
                protein * NutritionConstants.CALORIES_PER_GRAM_PROTEIN
                + carbs * NutritionConstants.CALORIES_PER_GRAM_CARBS
                + fat * NutritionConstants.CALORIES_PER_GRAM_FAT,
                1,
            ),
        }

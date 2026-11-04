"""
Handler - Calculate TPreviewTdeeQueryDEE preview without saving.
"""

import logging
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.tdee.preview_tdee_query import PreviewTdeeQuery
from src.domain.constants import NutritionConstants
from src.domain.mappers.activity_goal_mapper import ActivityGoalMapper
from src.domain.model.user import (
    JobType,
    MacroPreset,
    MacroTargets,
    Sex,
    TdeeRequest,
    UnitSystem,
)
from src.domain.services.tdee_service import TdeeCalculationService

logger = logging.getLogger(__name__)


# Job type base multipliers for response
JOB_TYPE_MULTIPLIERS = {
    "desk": 1.2,
    "on_feet": 1.4,
    "physical": 1.6,
}


# Versioned policy identifier shared with onboarding clients.  A preview must
# carry this value so clients can reject a result calculated under another policy.
ONBOARDING_PREVIEW_CALCULATION_CONTRACT = "onboarding_preview_v2"


@handles(PreviewTdeeQuery)
class PreviewTdeeQueryHandler(EventHandler[PreviewTdeeQuery, dict[str, Any]]):
    """Handler for previewing TDEE calculation without persisting."""

    def __init__(self, tdee_service: TdeeCalculationService = None):
        self.tdee_service = tdee_service or TdeeCalculationService()

    async def handle(self, query: PreviewTdeeQuery) -> dict[str, Any]:
        """Calculate TDEE preview without persisting."""
        # Map inputs using centralized mapper
        sex = Sex.MALE if query.sex.lower() == "male" else Sex.FEMALE
        job_type = JobType(query.job_type)
        goal = ActivityGoalMapper.map_goal(query.goal)
        unit_system = (
            UnitSystem.METRIC if query.unit_system == "metric" else UnitSystem.IMPERIAL
        )

        # Map training level if provided
        training_level = None
        if query.training_level:
            training_level = ActivityGoalMapper.map_training_level(query.training_level)

        # Create TDEE request with new job_type + training fields
        tdee_request = TdeeRequest(
            age=query.age,
            sex=sex,
            height=query.height,
            weight=query.weight,
            job_type=job_type,
            training_days_per_week=query.training_days_per_week,
            training_minutes_per_session=query.training_minutes_per_session,
            goal=goal,
            body_fat_pct=query.body_fat_percentage,
            unit_system=unit_system,
            training_level=training_level,
            macro_preset=(
                MacroPreset.KETO if query.diet_type == "keto" else MacroPreset.STANDARD
            ),
            weight_loss_kg_per_week=query.weight_loss_kg_per_week,
            weight_gain_kg_per_week=query.weight_gain_kg_per_week,
        )

        # Calculate TDEE
        result = self.tdee_service.calculate_tdee(tdee_request)
        macros = self._preview_macros(query, result.macros)

        # Baseline excludes planned workouts; logged movement credits them.
        base = JOB_TYPE_MULTIPLIERS.get(job_type.value, 1.2)

        return {
            "calculation_contract": ONBOARDING_PREVIEW_CALCULATION_CONTRACT,
            "bmr": result.bmr,
            "tdee": result.tdee,
            "goal": goal.value,
            "activity_multiplier": base,
            "formula_used": result.formula_used,
            "is_custom": self._has_custom_macros(query),
            "macro_preset": result.macro_preset.value,
            "target_revision": 0,
            "pace_range": result.pace_range.to_dict() if result.pace_range else None,
            "deficit_blocked_reason": result.deficit_blocked_reason,
            "gain_pace_blocked_reason": result.gain_pace_blocked_reason,
            "macros": {
                "protein": macros.protein,
                "carbs": macros.carbs,
                "fat": macros.fat,
                "calories": macros.calories,
            },
        }

    @staticmethod
    def _has_custom_macros(query: PreviewTdeeQuery) -> bool:
        return (
            query.custom_protein_g is not None
            and query.custom_carbs_g is not None
            and query.custom_fat_g is not None
        )

    def _preview_macros(
        self, query: PreviewTdeeQuery, calculated: MacroTargets
    ) -> MacroTargets:
        if self._has_custom_macros(query):
            return self._macro_targets(
                query.custom_protein_g,
                query.custom_carbs_g,
                query.custom_fat_g,
            )

        if query.requested_calories is not None:
            scale = query.requested_calories / calculated.calories
            return self._macro_targets(
                calculated.protein * scale,
                calculated.carbs * scale,
                calculated.fat * scale,
            )

        return self._macro_targets(calculated.protein, calculated.carbs, calculated.fat)

    @staticmethod
    def _macro_targets(protein: float, carbs: float, fat: float) -> MacroTargets:
        rounded_protein = round(protein, 1)
        rounded_carbs = round(carbs, 1)
        rounded_fat = round(fat, 1)
        calories = (
            rounded_protein * NutritionConstants.CALORIES_PER_GRAM_PROTEIN
            + rounded_carbs * NutritionConstants.CALORIES_PER_GRAM_CARBS
            + rounded_fat * NutritionConstants.CALORIES_PER_GRAM_FAT
        )
        return MacroTargets(
            calories=round(calories, 1),
            protein=rounded_protein,
            carbs=rounded_carbs,
            fat=rounded_fat,
        )

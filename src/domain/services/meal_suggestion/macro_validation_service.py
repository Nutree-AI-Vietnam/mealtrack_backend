"""
Macro validation service.

Two roles:
  validate_and_correct — AI macro sanity check (discovery path, legacy scan/parsing paths)
  validate_deterministic — sanity check + tier logging for deterministic recipe macros
"""

import logging
from typing import TYPE_CHECKING

from src.domain.model.nutrition.macros import Macros

if TYPE_CHECKING:
    from src.domain.services.meal_suggestion.nutrition_lookup_service import MealMacros

logger = logging.getLogger(__name__)


class MacroValidationService:
    """Validate AI-generated macros and sanity-check deterministic macros."""

    # Max acceptable divergence between derived and reported calories
    CALORIE_DIFF_THRESHOLD_PCT = 10.0

    def validate_deterministic(self, meal_macros: "MealMacros") -> "MealMacros":
        """Sanity check deterministic macros from NutritionLookupService.

        Logs tier distribution for observability. Returns meal_macros unchanged.
        This should always pass since calories are derived from macros.

        Args:
            meal_macros: MealMacros dataclass from NutritionLookupService.calculate_meal_macros()

        Returns:
            meal_macros unchanged — this is a validator/logger, not a transformer.
        """
        logger.info(
            "Macro sources: T1=%d, T2=%d, T3=%d, unresolved=%d",
            meal_macros.t1_count,
            meal_macros.t2_count,
            meal_macros.t3_count,
            meal_macros.unresolved_count,
        )

        if meal_macros.unresolved_count:
            logger.error(
                "%d ingredient(s) priced by no tier — the meal is short by "
                "whatever they carry: %s",
                meal_macros.unresolved_count,
                [i.name for i in meal_macros.ingredients
                 if i.source_tier == "unresolved"],
            )

        if meal_macros.calories <= 0:
            logger.error(
                "Zero/negative calories from deterministic calc: calories=%.1f | "
                "ingredients=%s",
                meal_macros.calories,
                [i.name for i in meal_macros.ingredients],
            )

        return meal_macros

    def validate_and_correct(self, macros: dict) -> dict:
        """Verify P*4 + C*4 + F*9 ≈ reported calories. Correct if off.

        Args:
            macros: Dict with calories, protein, carbs, fat keys.

        Returns:
            Corrected macros dict. Adds _validation metadata if corrected.
        """
        protein = max(0.0, macros.get("protein", 0.0))
        carbs = max(0.0, macros.get("carbs", 0.0))
        # Real meals always have some fat — floor at 3g to catch AI hallucinations
        fat = max(3.0, macros.get("fat", 0.0))
        fiber = max(0.0, macros.get("fiber", 0.0))
        reported_cal = macros.get("calories", 0.0)

        # Fiber-aware: net_carb*4 + fiber*2 + P*4 + F*9
        derived_cal = round(Macros.raw_total_calories(protein, carbs, fat, fiber), 1)

        # Clamp negatives
        macros["protein"] = protein
        macros["carbs"] = carbs
        macros["fat"] = fat
        macros["fiber"] = fiber

        if reported_cal and reported_cal > 0:
            diff_pct = abs(derived_cal - reported_cal) / reported_cal * 100
            if diff_pct > self.CALORIE_DIFF_THRESHOLD_PCT:
                # Trust macros over reported calories
                logger.warning(
                    "Macro validation corrected calories: %s → %s (%.1f%% off)",
                    reported_cal,
                    derived_cal,
                    diff_pct,
                )
                macros["calories"] = derived_cal
                macros["_validation"] = {
                    "corrected": True,
                    "original_calories": reported_cal,
                    "diff_pct": round(diff_pct, 1),
                }
                return macros

        # No correction needed — still derive from macros for consistency
        macros["calories"] = derived_cal
        return macros

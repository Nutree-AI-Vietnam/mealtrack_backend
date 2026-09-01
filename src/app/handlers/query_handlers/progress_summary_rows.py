"""Build one progress-summary day row from already-bucketed range data."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.domain.model.meal import MealStatus
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.services.progress_summary_window import logged_status_for_meal_count


def build_progress_day_row(
    day: date,
    *,
    meals: list[Any],
    burned_calories: float,
    hydration_ml: int,
    hydration_goal_ml: int,
    protein_target_g: float,
    target_calories: float,
    target_source: str,
    is_cheat_day: bool,
) -> dict[str, Any]:
    protein = 0.0
    carbs = 0.0
    fat = 0.0
    calories = 0.0
    meal_count = 0
    for meal in meals:
        if meal.status == MealStatus.INACTIVE:
            continue
        meal_count += 1
        if meal.nutrition and meal.status in (MealStatus.READY, MealStatus.ENRICHING):
            macros = meal.nutrition.macros
            if macros:
                protein += macros.protein or 0.0
                carbs += macros.carbs or 0.0
                fat += macros.fat or 0.0
                calories += effective_meal_calories(meal)
    return {
        "date": day.isoformat(),
        "protein_g": round(protein, 1),
        "carbs_g": round(carbs, 1),
        "fat_g": round(fat, 1),
        "calories": round(calories, 1),
        "target_calories": round(target_calories, 1),
        "target_source": target_source,
        "burned_calories": round(burned_calories, 1),
        "hydration_ml": int(hydration_ml),
        "hydration_goal_ml": int(hydration_goal_ml),
        "protein_target_g": round(protein_target_g, 1),
        "meal_count": meal_count,
        "logged_status": logged_status_for_meal_count(meal_count),
        "is_cheat_day": is_cheat_day,
    }

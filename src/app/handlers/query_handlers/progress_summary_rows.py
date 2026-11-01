"""Build one progress-summary day row from already-bucketed range data."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.app.handlers.query_handlers.progress_summary_trends import (
    trends_day_fields,
)
from src.domain.model.meal import MealStatus
from src.domain.model.nutrition.extra_nutrients import merge_meal_micros
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros_ops import merge_micros
from src.domain.model.supplement.day_totals import supplement_day_totals
from src.domain.services.meal_calorie_service import effective_meal_calories
from src.domain.services.nrf_score import highlight_amounts, nrf_progress_fields
from src.domain.services.progress_summary_window import logged_status_for_meal_count


def build_progress_day_row(
    day: date,
    *,
    meals: list[Any],
    burned_calories: float,
    hydration_ml: int,
    hydration_goal_ml: int,
    protein_target_g: float,
    fiber_target_g: float,
    target_calories: float,
    target_source: str,
    is_cheat_day: bool,
    drink_macros: Macros | None = None,
    drink_micros: Any = None,
    trends_movement: list[Any] | None = None,
    supplement_doses: list[Any] | None = None,
) -> dict[str, Any]:
    """One summary row; ``trends_movement`` (even empty) adds the Trends fields."""
    protein = 0.0
    carbs = 0.0
    fat = 0.0
    fiber = 0.0
    calories = 0.0
    meal_count = 0
    # Meals logged as food: a plain drink's legacy hydration meal row and a
    # caloric drink's macros count toward meal_count but not here.
    food_meal_count = 0
    day_micros = None
    for meal in meals:
        if meal.status == MealStatus.INACTIVE:
            continue
        meal_count += 1
        if meal.meal_type != "hydration":
            food_meal_count += 1
        if meal.nutrition and meal.status in (MealStatus.READY, MealStatus.ENRICHING):
            macros = meal.nutrition.macros
            if macros:
                protein += macros.protein or 0.0
                carbs += macros.carbs or 0.0
                fat += macros.fat or 0.0
                fiber += macros.fiber or 0.0
                calories += effective_meal_calories(meal)
            # Meal-level micros only, even when include=trends loaded the food
            # items, so a day's score and highlights match on both shapes.
            day_micros = merge_micros(
                day_micros,
                merge_meal_micros(getattr(meal.nutrition, "micros", None), None),
            )
    if drink_macros is not None:
        protein += drink_macros.protein or 0.0
        carbs += drink_macros.carbs or 0.0
        fat += drink_macros.fat or 0.0
        fiber += drink_macros.fiber or 0.0
        calories += drink_macros.total_calories
        day_micros = merge_micros(day_micros, drink_micros)
        if meal_count == 0 and (protein or carbs or fat or fiber):
            meal_count = 1
    nrf = nrf_progress_fields(protein, fiber, day_micros)
    # Highlights count doses as Home does; the food-quality score does not.
    supplements = supplement_day_totals(supplement_doses or [])
    highlights = merge_micros(day_micros, supplements.home_highlight_micros())
    row: dict[str, Any] = {
        "date": day.isoformat(),
        "protein_g": round(protein, 1),
        "carbs_g": round(carbs, 1),
        "fat_g": round(fat, 1),
        "fiber_g": round(fiber, 1),
        "fiber_target_g": fiber_target_g,
        "calories": round(calories, 1),
        "target_calories": round(target_calories, 1),
        "target_source": target_source,
        "burned_calories": round(burned_calories, 1),
        "hydration_ml": int(hydration_ml),
        "hydration_goal_ml": int(hydration_goal_ml),
        "protein_target_g": round(protein_target_g, 1),
        "meal_count": meal_count,
        "food_meal_count": food_meal_count,
        "logged_status": logged_status_for_meal_count(meal_count),
        "is_cheat_day": is_cheat_day,
        "nrf_quality": nrf["nrf_quality"],
        "nrf_coverage": nrf["nrf_coverage"],
        **highlight_amounts(highlights),
    }
    if trends_movement is not None:
        row.update(trends_day_fields(meals, drink_micros, trends_movement, supplements))
    return row

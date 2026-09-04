"""Size next-meal Discover targets for one slot, not the remaining day."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.services.meal_recommendation.calorie_allocation_policy import (
    CalorieAllocationPolicy,
)

_POLICY = CalorieAllocationPolicy()


@dataclass(frozen=True, slots=True)
class NextMealDiscoverTargets:
    calorie_target: int | None
    protein_target: float | None
    carbs_target: float | None
    fat_target: float | None


def next_meal_discover_targets(
    *,
    meal_slot: str,
    remaining_calories: float | None,
    remaining_protein_g: float | None = None,
    remaining_carbs_g: float | None = None,
    remaining_fat_g: float | None = None,
    daily_target_calories: float | None = None,
) -> NextMealDiscoverTargets:
    """Return Discover targets for one meal.

    Remaining daily macros are a ceiling, not the meal size. Lunch/dinner/breakfast
    use the same slot weights as meal recommendations; a snack is 10% of the day.
    """
    remaining = _positive(remaining_calories)
    if remaining_calories is not None and remaining is None:
        return NextMealDiscoverTargets(None, None, None, None)

    daily = _positive(daily_target_calories) or remaining
    if daily is None:
        return NextMealDiscoverTargets(None, None, None, None)

    try:
        slot_calories = _POLICY.target_for(int(round(daily)), meal_slot)
    except ValueError:
        return NextMealDiscoverTargets(None, None, None, None)

    if remaining is not None:
        slot_calories = min(slot_calories, int(round(remaining)))
    if slot_calories <= 0:
        return NextMealDiscoverTargets(None, None, None, None)

    ratio = slot_calories / remaining if remaining else None
    return NextMealDiscoverTargets(
        calorie_target=slot_calories,
        protein_target=_scale(remaining_protein_g, ratio),
        carbs_target=_scale(remaining_carbs_g, ratio),
        fat_target=_scale(remaining_fat_g, ratio),
    )


def _positive(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    return float(value)


def _scale(value: float | None, ratio: float | None) -> float | None:
    if value is None or ratio is None:
        return None
    scaled = value * ratio
    if scaled <= 0:
        return None
    return round(scaled, 1)

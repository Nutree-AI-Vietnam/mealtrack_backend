"""Deterministic lunch and dinner selection for a weekly plan."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    recipe_excluded_by_allergen,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    is_planner_eligible,
)


@dataclass(frozen=True)
class GeneratedSlot:
    day_index: int
    slot_index: int
    recipe_id: str | None


class WeeklyPlanGenerationService:
    """Generate stable selections without provider calls or persistence."""

    algorithm_version = "v1"

    def generate(
        self,
        meals: Iterable[CatalogMeal],
        *,
        user_id: str,
        week_start_date: str,
        daily_calories: int,
        preferences: WeeklyMealPlanPreferences,
    ) -> tuple[GeneratedSlot, ...]:
        all_meals = tuple(meals)
        hard_candidates = [
            meal for meal in all_meals if self._hard_eligible(meal, preferences)
        ]
        candidates = [
            meal for meal in hard_candidates if self._soft_eligible(meal, preferences)
        ] or hard_candidates
        candidates.sort(key=lambda meal: self._sort_key(meal, user_id, week_start_date))
        if not candidates:
            return tuple(
                GeneratedSlot(day, slot, None) for day in range(7) for slot in range(2)
            )

        target = max(1, round(daily_calories / 2))
        result: list[GeneratedSlot] = []
        for day in range(7):
            for slot in range(2):
                eligible = [
                    meal for meal in candidates if self._supports_slot(meal, slot)
                ]
                pool = eligible or candidates
                selected = min(
                    pool,
                    key=lambda meal: (
                        abs(meal.calories - target),
                        self._stable_rank(meal, user_id, week_start_date, day, slot),
                        meal.id,
                    ),
                )
                result.append(GeneratedSlot(day, slot, selected.id))
        return tuple(result)

    def _hard_eligible(
        self,
        meal: CatalogMeal,
        preferences: WeeklyMealPlanPreferences,
    ) -> bool:
        haystack = _haystack(meal)
        if "lunch" not in meal.meal_types and "dinner" not in meal.meal_types:
            return False
        if preferences.diet == "vegetarian" and _contains_any(haystack, _MEAT_WORDS):
            return False
        if preferences.diet == "no-pork" and _contains_any(haystack, _PORK_WORDS):
            return False
        if any(dislike in haystack for dislike in preferences.dislikes):
            return False
        if not is_planner_eligible(
            publication_status=meal.publication_status,
            nutrition_status=meal.nutrition_status,
            is_active=meal.is_active,
        ):
            return False
        # Codes are the planner constraint. Free-text allergen copy is not.
        if recipe_excluded_by_allergen(meal.allergen_codes, preferences.allergies):
            return False
        return True

    @staticmethod
    def _soft_eligible(
        meal: CatalogMeal, preferences: WeeklyMealPlanPreferences
    ) -> bool:
        if (
            preferences.cuisine
            and preferences.cuisine.casefold() not in meal.cuisine.casefold()
        ):
            return False
        if preferences.cooking_time == "30" and _total_minutes(meal) > 30:
            return False
        return True

    @staticmethod
    def _supports_slot(meal: CatalogMeal, slot_index: int) -> bool:
        return ("lunch" if slot_index == 0 else "dinner") in meal.meal_types

    @staticmethod
    def _sort_key(meal: CatalogMeal, user_id: str, week_start_date: str):
        return (
            meal.popularity_rank if meal.popularity_rank is not None else 2_147_483_647,
            hashlib.sha256(
                f"{user_id}:{week_start_date}:{meal.id}".encode()
            ).hexdigest(),
            meal.id,
        )

    @staticmethod
    def _stable_rank(
        meal: CatalogMeal, user_id: str, week_start_date: str, day: int, slot: int
    ) -> str:
        return hashlib.sha256(
            f"{user_id}:{week_start_date}:{day}:{slot}:{meal.id}".encode()
        ).hexdigest()


_MEAT_WORDS = frozenset(
    {
        "beef",
        "chicken",
        "pork",
        "fish",
        "shrimp",
        "turkey",
        "meat",
        "thịt",
        "gà",
        "bò",
        "cá",
        "tôm",
    }
)
_PORK_WORDS = frozenset({"pork", "ham", "bacon", "sausage", "thịt heo", "thịt lợn"})


def _haystack(meal: CatalogMeal) -> str:
    values = [meal.name, meal.cuisine, meal.description or "", meal.allergens or ""]
    values.extend(ingredient.name for ingredient in meal.ingredients)
    return " ".join(values).casefold()


def _contains_any(value: str, words: Iterable[str]) -> bool:
    return any(word in value for word in words)


def _total_minutes(meal: CatalogMeal) -> int:
    return int(meal.prep_time_minutes or 0) + int(meal.cook_time_minutes or 0)

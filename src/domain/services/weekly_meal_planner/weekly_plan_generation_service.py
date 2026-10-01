"""Deterministic lunch and dinner selection for a weekly plan."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    normalize_allergen_code,
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
        used_counts: dict[str, int] = {}
        for day in range(7):
            day_used: set[str] = set()
            for slot in range(2):
                eligible = [
                    meal for meal in candidates if self._supports_slot(meal, slot)
                ]
                if not eligible:
                    result.append(GeneratedSlot(day, slot, None))
                    continue
                selected = min(
                    eligible,
                    key=lambda meal: (
                        1 if meal.id in day_used else 0,
                        used_counts.get(meal.id, 0),
                        abs(meal.calories - target),
                        self._stable_rank(meal, user_id, week_start_date, day, slot),
                        meal.id,
                    ),
                )
                used_counts[selected.id] = used_counts.get(selected.id, 0) + 1
                day_used.add(selected.id)
                result.append(GeneratedSlot(day, slot, selected.id))
        return tuple(result)

    def _hard_eligible(
        self,
        meal: CatalogMeal,
        preferences: WeeklyMealPlanPreferences,
    ) -> bool:
        haystack = _haystack(meal)
        if _is_non_meal_recipe(meal):
            return False
        if "lunch" not in meal.meal_types and "dinner" not in meal.meal_types:
            return False
        if preferences.diet == "vegetarian" and _contains_any(haystack, _MEAT_WORDS):
            return False
        if preferences.diet == "no-pork" and _contains_any(haystack, _PORK_WORDS):
            return False
        if any(dislike.casefold() in haystack for dislike in preferences.dislikes):
            return False
        meal_allergens = {normalize_allergen_code(code) for code in meal.allergen_codes}
        if any(
            normalize_allergen_code(dislike) in meal_allergens
            for dislike in preferences.dislikes
        ):
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

    def is_hard_eligible(
        self,
        meal: CatalogMeal,
        preferences: WeeklyMealPlanPreferences,
    ) -> bool:
        """Expose the generation hard-filter for externally proposed swaps."""
        return self._hard_eligible(meal, preferences)

    def is_soft_eligible(
        self,
        meal: CatalogMeal,
        preferences: WeeklyMealPlanPreferences,
    ) -> bool:
        """Check cuisine and cooking-time preferences for candidate ranking."""
        return self._soft_eligible(meal, preferences)

    @staticmethod
    def supports_slot(meal: CatalogMeal, slot_index: int) -> bool:
        """Return whether a catalog meal is suitable for lunch or dinner."""
        return WeeklyPlanGenerationService._supports_slot(meal, slot_index)

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
        "tép",
        "mực",
        "sườn",
        "heo",
        "lợn",
        "ba chỉ",
        "cua",
        "ốc",
        "nghêu",
        "sò",
        "hàu",
        "prawn",
        "squid",
        "octopus",
        "crab",
        "clam",
        "oyster",
        "seafood",
        "oyster sauce",
        "dầu hào",
    }
)
_PORK_WORDS = frozenset({"pork", "ham", "bacon", "sausage", "thịt heo", "thịt lợn"})
_NON_MEAL_TITLE_WORDS = frozenset(
    {
        "beverage",
        "cocktail",
        "mocktail",
        "drink",
        "smoothie",
        "juice",
        "lemonade",
        "milkshake",
        "latte",
        "soda",
        "bingsu",
        "pudding",
        "dessert",
        "ice cream",
        "gelato",
        "cheesecake",
        "brownie",
        "french toast",
        "milo cube",
        "bánh khoai mỡ",
        "bánh tuyết",
        "bắp rang",
        "popcorn",
        "bánh mì dẹt",
        "flatbread",
        "bánh con sùng",
        "con sùng",
        "muối chua",
        "mẹo",
        "sinh tố",
        "nước ép",
        "nước chanh",
        "đá chanh",
        "trà sữa",
        "sữa macca",
        "sữa",
        "pha chế",
        "bánh mì nướng quế",
        "panna cotta",
        "chè",
        "cà phê",
        "cafe",
        "bài thuốc",
        "thuốc trị",
        "trị ho",
        "cough remedy",
        "herbal remedy",
        "medicine",
        "medicinal treatment",
    }
)


def _haystack(meal: CatalogMeal) -> str:
    values = [meal.name, meal.cuisine, meal.description or "", meal.allergens or ""]
    values.extend(ingredient.name for ingredient in meal.ingredients)
    return " ".join(values).casefold()


def _contains_any(value: str, words: Iterable[str]) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(word)}(?!\w)", value) is not None
        for word in words
    )


def _is_non_meal_recipe(meal: CatalogMeal) -> bool:
    return is_non_meal_title(meal.name, meal.tag)


def is_non_meal_title(title: str, tag: str | None = None) -> bool:
    """Identify catalog titles that should not be offered as lunch or dinner."""
    return _contains_any(f"{title} {tag or ''}".casefold(), _NON_MEAL_TITLE_WORDS)


def _total_minutes(meal: CatalogMeal) -> int:
    return int(meal.prep_time_minutes or 0) + int(meal.cook_time_minutes or 0)

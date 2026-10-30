"""Deterministic breakfast, lunch, and dinner selection for a weekly plan."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import (
    SLOT_MEAL_TYPES,
    WEEKLY_DAYS,
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    meal_type_for_slot,
)
from src.domain.services.meal_recommendation.calorie_allocation_policy import (
    CalorieAllocationPolicy,
)
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    normalize_allergen_code,
    recipe_excluded_by_allergen,
)
from src.domain.services.weekly_meal_planner.meal_practicality import (
    practicality_rank,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    is_planner_eligible,
)

# Recorded on plans the compact week generator selected.
COMPACT_ALGORITHM_VERSION = "v3-compact"

_BREAKFAST = SLOT_MEAL_TYPES.index("breakfast")
_LUNCH = SLOT_MEAL_TYPES.index("lunch")
_DINNER = SLOT_MEAL_TYPES.index("dinner")
# A compact week alternates this many breakfasts.
_BREAKFAST_ROTATION = 2
# Meals whose calories sit this close to a slot's target count as equally
# good, so a compact week can pick among them the one adding fewest new
# fresh ingredients.
_REUSE_CALORIE_BAND_KCAL = 80
_REUSE_CALORIE_BAND_SHARE = 0.15
# A meal with no known ingredients must not look like it adds nothing.
_UNKNOWN_FRESH_COUNT = 99


@dataclass(frozen=True)
class GeneratedSlot:
    day_index: int
    slot_index: int
    recipe_id: str | None


class WeeklyPlanGenerationService:
    """Generate stable selections without provider calls or persistence."""

    algorithm_version = "v2"
    compact_algorithm_version = COMPACT_ALGORITHM_VERSION

    def generate(
        self,
        meals: Iterable[CatalogMeal],
        *,
        user_id: str,
        week_start_date: str,
        daily_calories: int,
        preferences: WeeklyMealPlanPreferences,
    ) -> tuple[GeneratedSlot, ...]:
        prepared = self._slot_candidates(
            meals,
            user_id=user_id,
            week_start_date=week_start_date,
            daily_calories=daily_calories,
            preferences=preferences,
        )
        if prepared is None:
            return _empty_week()
        _, slot_candidates = prepared
        result: list[GeneratedSlot] = []
        used_counts: dict[str, int] = {}
        for day in range(WEEKLY_DAYS):
            day_used: set[str] = set()
            for slot in range(WEEKLY_SLOTS_PER_DAY):
                eligible = slot_candidates[slot]
                if not eligible:
                    result.append(GeneratedSlot(day, slot, None))
                    continue
                selected, _ = min(
                    eligible,
                    key=lambda item: (
                        1 if item[0].id in day_used else 0,
                        *_practicality(item[0], slot),
                        used_counts.get(item[0].id, 0),
                        item[1],
                        self._stable_rank(item[0], user_id, week_start_date, day, slot),
                        item[0].id,
                    ),
                )
                used_counts[selected.id] = used_counts.get(selected.id, 0) + 1
                day_used.add(selected.id)
                result.append(GeneratedSlot(day, slot, selected.id))
        return tuple(result)

    def generate_compact(
        self,
        meals: Iterable[CatalogMeal],
        *,
        user_id: str,
        week_start_date: str,
        daily_calories: int,
        preferences: WeeklyMealPlanPreferences,
        basket_ids: Mapping[str, frozenset[int]],
        fixed: Mapping[tuple[int, int], str] | None = None,
    ) -> tuple[GeneratedSlot, ...]:
        """Plan a week around fewer dishes and one shared grocery basket.

        Two breakfasts alternate through the week. Each dinner is cooked for
        two meals and eaten again at the next day's lunch, so only the first
        lunch is cooked on its own: about ten dishes instead of 21. Each dish
        cooked prefers, among meals equally close to the slot's calorie
        target, the one adding the fewest fresh ingredients the week does not
        already buy, then the one whose new ingredients most other eligible
        dishes also use, so the basket starts from common ingredients.
        ``basket_ids`` maps a recipe to its fresh grocery ids.

        ``fixed`` holds logged meals by (day, slot). They stay in place and
        count toward the basket, and a logged dinner still feeds the next
        lunch.
        """
        prepared = self._slot_candidates(
            meals,
            user_id=user_id,
            week_start_date=week_start_date,
            daily_calories=daily_calories,
            preferences=preferences,
        )
        if prepared is None:
            return _empty_week()
        targets, slot_candidates = prepared
        fixed = fixed or {}
        eligible_ids = [
            {meal.id for meal, _ in candidates} for candidates in slot_candidates
        ]
        frequency = Counter(
            ingredient
            for recipe_id in set().union(*eligible_ids)
            for ingredient in basket_ids.get(recipe_id, ())
        )
        basket: set[int] = set()
        for logged_id in fixed.values():
            basket.update(basket_ids.get(logged_id, ()))
        cooked: dict[str, int] = {}
        breakfasts: list[str] = []
        chosen: dict[tuple[int, int], str | None] = {}

        def cook(
            day: int, slot: int, day_used: set[str], *, feeds_next_lunch: bool
        ) -> str | None:
            eligible = slot_candidates[slot]
            if not eligible:
                return None
            selected, _ = min(
                eligible,
                key=lambda item: (
                    1 if item[0].id in day_used else 0,
                    *_practicality(item[0], slot),
                    cooked.get(item[0].id, 0),
                    1
                    if feeds_next_lunch and item[0].id not in eligible_ids[_LUNCH]
                    else 0,
                    *_reuse_rank(
                        item[0].id,
                        item[1],
                        targets[slot],
                        basket_ids,
                        basket,
                        frequency,
                    ),
                    item[1],
                    self._stable_rank(item[0], user_id, week_start_date, day, slot),
                    item[0].id,
                ),
            )
            cooked[selected.id] = cooked.get(selected.id, 0) + 1
            basket.update(basket_ids.get(selected.id, ()))
            return selected.id

        for day in range(WEEKLY_DAYS):
            day_used: set[str] = set()
            for slot in range(WEEKLY_SLOTS_PER_DAY):
                recipe_id: str | None
                last_dinner = chosen.get((day - 1, _DINNER))
                if (day, slot) in fixed:
                    recipe_id = fixed[(day, slot)]
                elif slot == _BREAKFAST and day >= _BREAKFAST_ROTATION and breakfasts:
                    recipe_id = breakfasts[day % len(breakfasts)]
                elif (
                    slot == _LUNCH
                    and last_dinner in eligible_ids[_LUNCH]
                    and last_dinner not in day_used
                ):
                    recipe_id = last_dinner
                else:
                    recipe_id = cook(
                        day,
                        slot,
                        day_used,
                        feeds_next_lunch=slot == _DINNER
                        and day + 1 < WEEKLY_DAYS
                        and (day + 1, _LUNCH) not in fixed,
                    )
                if (
                    slot == _BREAKFAST
                    and day < _BREAKFAST_ROTATION
                    and recipe_id in eligible_ids[_BREAKFAST]
                    and recipe_id not in breakfasts
                ):
                    breakfasts.append(recipe_id)
                chosen[(day, slot)] = recipe_id
                if recipe_id is not None:
                    day_used.add(recipe_id)
        return tuple(
            GeneratedSlot(day, slot, chosen[(day, slot)])
            for day in range(WEEKLY_DAYS)
            for slot in range(WEEKLY_SLOTS_PER_DAY)
        )

    def _slot_candidates(
        self,
        meals: Iterable[CatalogMeal],
        *,
        user_id: str,
        week_start_date: str,
        daily_calories: int,
        preferences: WeeklyMealPlanPreferences,
    ) -> tuple[tuple[int, ...], list[list[tuple[CatalogMeal, int]]]] | None:
        """Each slot's calorie target and eligible meals with their calorie gap.

        Returns None when no meal fits the preferences at all.
        """
        all_meals = tuple(meals)
        hard_candidates = [
            meal for meal in all_meals if self._hard_eligible(meal, preferences)
        ]
        candidates = [
            meal for meal in hard_candidates if self._soft_eligible(meal, preferences)
        ] or hard_candidates
        candidates.sort(key=lambda meal: self._sort_key(meal, user_id, week_start_date))
        if not candidates:
            return None
        targets = _slot_calorie_targets(daily_calories)
        return targets, [
            [
                (meal, abs(meal.calories - targets[slot]))
                for meal in candidates
                if self._supports_slot(meal, slot)
            ]
            for slot in range(WEEKLY_SLOTS_PER_DAY)
        ]

    def _hard_eligible(
        self,
        meal: CatalogMeal,
        preferences: WeeklyMealPlanPreferences,
    ) -> bool:
        features = meal.selection_features
        haystack = features.haystack if features else _haystack(meal)
        non_meal = features.non_meal if features else _is_non_meal_recipe(meal)
        if non_meal:
            return False
        if not any(
            meal_type_for_slot(slot) in meal.meal_types
            for slot in range(WEEKLY_SLOTS_PER_DAY)
        ):
            return False
        if preferences.diet == "vegetarian" and (
            features.contains_meat if features else _contains_any(haystack, _MEAT_WORDS)
        ):
            return False
        if preferences.diet == "no-pork" and (
            features.contains_pork if features else _contains_any(haystack, _PORK_WORDS)
        ):
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
        """Return whether a catalog meal is suitable for this breakfast, lunch, or dinner slot."""
        return WeeklyPlanGenerationService._supports_slot(meal, slot_index)

    @staticmethod
    def selection_rank(meal: CatalogMeal, slot_index: int) -> tuple[int, int]:
        """Prefer a simple meal that fits this slot over a closer calorie match."""
        return _practicality(meal, slot_index)

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
        return meal_type_for_slot(slot_index) in meal.meal_types

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
    words = tuple(sorted(words))
    if not words:
        return False
    return _word_pattern(words).search(value) is not None


@lru_cache(maxsize=32)
def _word_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(word) for word in words) + r")(?!\w)"
    )


def _is_non_meal_recipe(meal: CatalogMeal) -> bool:
    return is_non_meal_title(meal.name, meal.tag)


def _practicality(meal: CatalogMeal, slot_index: int) -> tuple[int, int]:
    features = meal.selection_features
    non_meal = features.non_meal if features else _is_non_meal_recipe(meal)
    minutes = _total_minutes(meal)
    return practicality_rank(
        meal.name,
        meal_type_for_slot(slot_index),
        total_minutes=minutes or None,
        ingredient_count=len(meal.ingredients),
        non_meal=non_meal,
    )


def _reuse_rank(
    meal_id: str,
    calorie_gap: float,
    target: int,
    basket_ids: Mapping[str, frozenset[int]],
    basket: set[int],
    frequency: Mapping[int, int],
) -> tuple[int, int, int]:
    """Rank by calorie band, fresh ingredients new to the week, then how many
    eligible dishes use those new ingredients (more first)."""
    band = int(
        calorie_gap // max(_REUSE_CALORIE_BAND_KCAL, target * _REUSE_CALORIE_BAND_SHARE)
    )
    fresh = basket_ids.get(meal_id)
    if fresh is None:
        return (band, _UNKNOWN_FRESH_COUNT, 0)
    new = fresh - basket
    return (band, len(new), -sum(frequency.get(item, 0) for item in new))


def leftover_lunch_cook_days(plan: WeeklyMealPlan) -> dict[tuple[int, int], int]:
    """Lunches a compact week eats as the previous dinner, by cook day.

    Groceries for such a lunch are bought and cooked with that dinner. Other
    plans, and a lunch the user has since swapped, keep their own day.
    """
    if plan.algorithm_version != COMPACT_ALGORITHM_VERSION:
        return {}
    recipes = {(slot.day_index, slot.slot_index): slot.recipe_id for slot in plan.slots}
    return {
        (day, _LUNCH): day - 1
        for day in range(1, WEEKLY_DAYS)
        if recipes.get((day, _LUNCH)) is not None
        and recipes.get((day, _LUNCH)) == recipes.get((day - 1, _DINNER))
    }


def _empty_week() -> tuple[GeneratedSlot, ...]:
    return tuple(
        GeneratedSlot(day, slot, None)
        for day in range(WEEKLY_DAYS)
        for slot in range(WEEKLY_SLOTS_PER_DAY)
    )


def _slot_calorie_targets(daily_calories: int) -> tuple[int, ...]:
    allocations = CalorieAllocationPolicy().allocate(max(1, daily_calories))
    return tuple(
        allocations[meal_type_for_slot(slot)] for slot in range(WEEKLY_SLOTS_PER_DAY)
    )


def is_non_meal_title(title: str, tag: str | None = None) -> bool:
    """Identify catalog titles that should not be offered as a planned meal."""
    return _contains_any(f"{title} {tag or ''}".casefold(), _NON_MEAL_TITLE_WORDS)


def _total_minutes(meal: CatalogMeal) -> int:
    return int(meal.prep_time_minutes or 0) + int(meal.cook_time_minutes or 0)

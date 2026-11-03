"""Deterministic breakfast, lunch, and dinner selection for a weekly plan."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.nutrition.macros import Macros
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
from src.domain.services.weekly_meal_planner.grocery_projection import (
    fresh_ingredient_id,
)
from src.domain.services.weekly_meal_planner.meal_locale import (
    DEFAULT_LOCALE,
    MealLocale,
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
        last_week: frozenset[str] = frozenset(),
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
                        # Without this the same profile and catalog give the
                        # same week every week: the calorie gap below decides.
                        1 if item[0].id in last_week else 0,
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
        last_week: frozenset[str] = frozenset(),
        basket_target: int | None = None,
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

        ``basket_target`` caps how many fresh ingredients the week should buy.
        While the basket has room the choice is the same as without a budget;
        once it is full, reusing what is already bought outranks calorie
        closeness. It is a target, not a hard rule: a slot is always filled,
        even when every remaining dish adds something new.

        ``fixed`` holds logged meals by (day, slot). They stay in place and
        count toward the basket, and a logged dinner still feeds the next
        lunch. Dishes in ``last_week`` come after ones the user has not just
        had, so consecutive weeks differ while the catalog allows it.
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
                    1 if item[0].id in last_week else 0,
                    *_reuse_rank(
                        item[0].id,
                        item[1],
                        targets[slot],
                        basket_ids,
                        basket,
                        frequency,
                        None
                        if basket_target is None
                        else max(0, basket_target - len(basket)),
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
    allowance: int | None = None,
) -> tuple[int, int, int, int]:
    """Rank a dish by how it fits the week's calories and its grocery basket.

    Without a budget (``allowance`` is None) calories lead: rank by calorie
    band, then fresh ingredients new to the week, then how many eligible
    dishes use those new ingredients.

    With a budget, the basket leads. ``allowance`` is how many more fresh
    ingredients the week may still buy, so a dish that would push past it
    sorts last. Within the budget the dish whose new ingredients the most
    other dishes also use comes first, which starts the week inside a group
    of dishes that shop alike, and calories decide last. Picking by calories
    first never reaches a small basket: the first dish of a shared group
    costs as much as a one-off dish, so the week never enters the group.
    """
    band = int(
        calorie_gap // max(_REUSE_CALORIE_BAND_KCAL, target * _REUSE_CALORIE_BAND_SHARE)
    )
    fresh = basket_ids.get(meal_id)
    if fresh is None:
        new_count, reuse = _UNKNOWN_FRESH_COUNT, 0
    else:
        new = fresh - basket
        new_count = len(new)
        reuse = -sum(frequency.get(item, 0) for item in new)
    if allowance is None:
        return (0, band, new_count, reuse)
    return (max(0, new_count - allowance), reuse, new_count, band)


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


# ---------------------------------------------------------------------------
# Composing a meal
#
# A catalog recipe is raw material, not a fixed menu item. Serving one as it
# was written leaves the plan at the mercy of whatever portion the recipe was
# authored for, which is why today's weeks miss the day's macros: they plan
# calories only, one dish per slot, with no portion and no rice.
#
# compose_day() builds each meal instead: a dish, how much of it, and how much
# rice goes with it, chosen together against what is left of the day's four
# macro targets. The catalog supplies the candidates; the user's targets decide
# what is actually served.
# ---------------------------------------------------------------------------

# The staple's numbers come from the locale pack. These names stay because
# the API, the materializer and the app all read the default locale's values.
RICE_STEP_GRAMS = DEFAULT_LOCALE.staple_step_grams
RICE_STEP_MACROS = DEFAULT_LOCALE.staple_step_macros  # protein, carbs, fat
MAX_RICE_STEPS = DEFAULT_LOCALE.max_staple_steps
# A rice dish is eaten with rice: never plan one with none.
MIN_RICE_STEPS = DEFAULT_LOCALE.min_staple_steps
# Portions POST /slots/{id}/log accepts today. Planning outside this set would
# give the user a meal they cannot log as planned.
LOGGABLE_PORTIONS = (0.5, 1.0, 1.5, 2.0)
# No single meal may take more than this share of the day's calorie target.
MEAL_KCAL_SHARE_CAP = 0.45
# Weights on the daily macro miss. Protein counts double when short: eating
# under the protein target is worse than going over it.
_MACRO_WEIGHTS = (1.0, 1.0, 0.5, 0.5)  # kcal, protein, carbs, fat
_PROTEIN_SHORTFALL_WEIGHT = 1.5
# Small nudges toward a plain serving, so odd portions need to earn their place.
_PORTION_STEP_COST = 0.01
_EXTRA_RICE_COST = 0.01
_COMFORTABLE_RICE_STEPS = 2
# Dominates every macro term: a plan must not starve a day or pile it into one
# meal.
_RULE_BREAK_COST = 1.0
# Each repeat of a dish costs more than the last, so a week spreads out
# instead of serving its best-fitting dish seven times.
_REPEAT_COST = 0.15
# The usual split of a day across its slots. Used only to guess what the
# slots after this one will bring.
_SLOT_CALORIE_SHARES = DEFAULT_LOCALE.slot_calorie_shares


@dataclass(frozen=True)
class DayTargets:
    """What one day of the plan should add up to."""

    kcal: float
    protein: float
    carbs: float
    fat: float
    # Lowest calories the day may be planned at (clinical floor).
    floor_kcal: float = 0.0


@dataclass(frozen=True)
class ComposedSlot:
    """One planned meal: the dish, how much of it, and the rice beside it."""

    day_index: int
    slot_index: int
    recipe_id: str | None
    portion: float = 1.0
    rice_steps: int = 0
    # The step size of the locale this slot was composed for, so the grams
    # stay right for a plan made under another region's rules.
    staple_step_grams: int = RICE_STEP_GRAMS

    @property
    def rice_grams(self) -> int:
        return self.rice_steps * self.staple_step_grams


def meal_macros(meal: CatalogMeal) -> tuple[float, float, float, float]:
    """Protein, carbs, fat and fiber of one serving, as floats."""
    return (
        float(meal.protein_g or 0),
        float(meal.carbs_g or 0),
        float(meal.fat_g or 0),
        float(meal.fiber_g or 0),
    )


def _serving(
    meal: CatalogMeal,
    portion: float,
    rice_steps: int,
    staple_macros: tuple[float, float, float] = RICE_STEP_MACROS,
) -> tuple[float, float, float, float]:
    """Kcal, protein, carbs and fat of a dish at a portion, with its rice."""
    protein, carbs, fat, fiber = meal_macros(meal)
    protein, carbs, fat, fiber = (
        protein * portion,
        carbs * portion,
        fat * portion,
        fiber * portion,
    )
    rice_protein, rice_carbs, rice_fat = staple_macros
    protein += rice_protein * rice_steps
    carbs += rice_carbs * rice_steps
    fat += rice_fat * rice_steps
    return (
        Macros.raw_total_calories(protein, carbs, fat, fiber),
        protein,
        carbs,
        fat,
    )


def _macro_cost(
    totals: tuple[float, float, float, float], targets: DayTargets
) -> float:
    """Weighted relative miss across the day's four macro targets."""
    wanted = (targets.kcal, targets.protein, targets.carbs, targets.fat)
    cost = 0.0
    for index, (actual, target) in enumerate(zip(totals, wanted, strict=True)):
        if target <= 0:
            continue
        weight = _MACRO_WEIGHTS[index]
        if index == 1 and actual < target:
            weight *= _PROTEIN_SHORTFALL_WEIGHT
        cost += weight * abs(actual - target) / target
    return cost


def _options(
    meal: CatalogMeal,
    *,
    takes_rice: bool,
    cap_kcal: float,
    locale: MealLocale = DEFAULT_LOCALE,
) -> list[tuple[float, int, tuple[float, float, float, float]]]:
    """Every way this dish can be served, smallest first.

    A serving over the per-meal calorie cap is dropped, unless the dish has no
    serving under it: a slot is always filled.
    """
    rice_range = (
        range(locale.min_staple_steps, locale.max_staple_steps + 1)
        if takes_rice
        else range(0, 1)
    )
    every = [
        (portion, steps, _serving(meal, portion, steps, locale.staple_step_macros))
        for portion in LOGGABLE_PORTIONS
        for steps in rice_range
    ]
    within = [option for option in every if option[2][0] <= cap_kcal]
    if within:
        return within
    return [min(every, key=lambda option: option[2][0])]


def _serving_cost(portion: float, rice_steps: int) -> float:
    steps_from_plain = abs(
        LOGGABLE_PORTIONS.index(portion) - LOGGABLE_PORTIONS.index(1.0)
    )
    return _PORTION_STEP_COST * steps_from_plain + _EXTRA_RICE_COST * max(
        0, rice_steps - _COMFORTABLE_RICE_STEPS
    )


def compose_day(
    slot_candidates: tuple[tuple[CatalogMeal, ...], ...],
    targets: DayTargets,
    *,
    day_index: int = 0,
    rice_dishes: frozenset[str] = frozenset(),
    locale: MealLocale = DEFAULT_LOCALE,
    shares: tuple[float, ...] | None = None,
    polish_passes: int = 3,
    used_counts: Mapping[str, int] | None = None,
) -> tuple[ComposedSlot, ...]:
    """Compose one day: a dish, a portion and its rice for every slot.

    Each slot is filled against what the day still needs, assuming the slots
    after it bring their usual share. A polish pass then re-picks each slot
    knowing the rest of the day, which is what turns a run of locally sensible
    meals into a day that adds up.

    ``used_counts`` is how often each dish has already been served this week.
    Every repeat costs more than the last, and no dish is served twice in one
    day, so fitting the targets well does not turn into the same dinner every
    night.
    """
    cap_kcal = MEAL_KCAL_SHARE_CAP * targets.kcal if targets.kcal > 0 else float("inf")
    shares = locale.slot_calorie_shares if shares is None else shares
    served_before = dict(used_counts or {})
    slot_count = len(slot_candidates)
    chosen: list[ComposedSlot | None] = [None] * slot_count
    served: list[tuple[float, float, float, float]] = [
        (0.0, 0.0, 0.0, 0.0)
    ] * slot_count

    def totals(skip: int | None = None) -> tuple[float, float, float, float]:
        return tuple(
            sum(
                served[index][macro]
                for index in range(slot_count)
                if index != skip and chosen[index] is not None
            )
            for macro in range(4)
        )

    def expected(remaining: Iterable[int]) -> tuple[float, float, float, float]:
        """What the slots still to fill should bring, at their usual share."""
        share = sum(
            shares[index] if index < len(shares) else 0.0 for index in remaining
        )
        return (
            targets.kcal * share,
            targets.protein * share,
            targets.carbs * share,
            targets.fat * share,
        )

    def best_for(slot: int, *, rest: Iterable[int]) -> ComposedSlot | None:
        candidates = slot_candidates[slot]
        if not candidates:
            return None
        base = totals(skip=slot)
        ahead = expected(rest)
        elsewhere_today = {
            chosen[other].recipe_id
            for other in range(slot_count)
            if other != slot and chosen[other] is not None
        }
        best: tuple[float, str, ComposedSlot, tuple[float, ...]] | None = None
        for meal in candidates:
            if meal.id in elsewhere_today:
                continue
            takes_rice = meal.id in rice_dishes and slot in locale.staple_slots
            # The n-th serving of a dish costs n times the first.
            repeat = _REPEAT_COST * served_before.get(meal.id, 0)
            for portion, steps, serving in _options(
                meal, takes_rice=takes_rice, cap_kcal=cap_kcal, locale=locale
            ):
                projected = tuple(
                    base[macro] + serving[macro] + ahead[macro] for macro in range(4)
                )
                cost = (
                    _macro_cost(projected, targets)
                    + _serving_cost(portion, steps)
                    + repeat
                )
                if serving[0] > cap_kcal:
                    cost += _RULE_BREAK_COST
                key = (cost, meal.id)
                if best is None or key < (best[0], best[1]):
                    best = (
                        cost,
                        meal.id,
                        ComposedSlot(
                            day_index,
                            slot,
                            meal.id,
                            portion,
                            steps,
                            locale.staple_step_grams,
                        ),
                        serving,
                    )
        if best is None:
            return None
        served[slot] = best[3]
        return best[2]

    for slot in range(slot_count):
        chosen[slot] = best_for(slot, rest=range(slot + 1, slot_count))
        if chosen[slot] is not None and chosen[slot].recipe_id:
            served_before[chosen[slot].recipe_id] = (
                served_before.get(chosen[slot].recipe_id, 0) + 1
            )

    for _ in range(polish_passes):
        before = tuple(chosen)
        for slot in range(slot_count):
            if not slot_candidates[slot]:
                continue
            previous = chosen[slot]
            if previous is not None and previous.recipe_id:
                served_before[previous.recipe_id] -= 1
            chosen[slot] = best_for(slot, rest=())
            if chosen[slot] is not None and chosen[slot].recipe_id:
                served_before[chosen[slot].recipe_id] = (
                    served_before.get(chosen[slot].recipe_id, 0) + 1
                )
        if tuple(chosen) == before:
            break

    return tuple(
        chosen[slot] or ComposedSlot(day_index, slot, None)
        for slot in range(slot_count)
    )


def day_totals(
    composed: Iterable[ComposedSlot], meals_by_id: Mapping[str, CatalogMeal]
) -> tuple[float, float, float, float]:
    """Kcal, protein, carbs and fat a composed day actually delivers."""
    total = [0.0, 0.0, 0.0, 0.0]
    for slot in composed:
        meal = meals_by_id.get(slot.recipe_id or "")
        if meal is None:
            continue
        for index, value in enumerate(_serving(meal, slot.portion, slot.rice_steps)):
            total[index] += value
    return tuple(total)


# ---------------------------------------------------------------------------
# Finding reference dishes
#
# Treating the catalog as a book of ideas means a slot is not filled by
# "the next eligible row" but by the dishes most worth looking at: ones that
# can be served near the slot's targets, that shop from the basket the week is
# already buying, and that use food the user likes.
#
# rank_reference_dishes() produces that shortlist. The planner can serve it
# directly, and a generator can hand it to a model as the examples to work
# from. Callers pass meals that already satisfy the hard rules: this ranks,
# it never decides what is allowed.
#
# The semantic leg is injected. Meaning-based matching needs stored vectors,
# which nothing writes yet, so until then the other legs carry the score and
# its weight is shared out among them.
# ---------------------------------------------------------------------------

_FIT_WEIGHT = 0.4
_BASKET_WEIGHT = 0.25
_TASTE_WEIGHT = 0.2
_SEMANTIC_WEIGHT = 0.15
# A serving this far off the slot's calorie target scores nothing for fit.
_FIT_TOLERANCE = 0.5


@dataclass(frozen=True)
class SlotBrief:
    """What a slot is looking for."""

    slot_index: int
    kcal_target: float
    cuisine: str | None = None
    # Fresh grocery ids the week already buys.
    basket: frozenset[int] = frozenset()
    # Fresh grocery ids the user eats often.
    liked_ingredients: frozenset[int] = frozenset()
    exclude_recipe_ids: frozenset[str] = frozenset()
    takes_rice: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ScoredDish:
    """A reference dish, why it was shortlisted, and how strongly."""

    meal: CatalogMeal
    score: float
    fit: float
    basket: float
    taste: float
    semantic: float

    @property
    def reasons(self) -> tuple[str, ...]:
        """Plain labels for an explanation or a prompt."""
        labels = []
        if self.fit >= 0.8:
            labels.append("fits the slot's calories")
        if self.basket >= 0.5:
            labels.append("mostly uses what the week already buys")
        if self.taste >= 0.5:
            labels.append("uses food the user eats often")
        if self.semantic >= 0.5:
            labels.append("close to what was asked for")
        return tuple(labels)


def dish_fresh_ingredient_ids(meal: CatalogMeal) -> frozenset[int]:
    """Grocery ids a dish needs, seasonings and kitchen staples aside."""
    ids = set()
    for line in meal.ingredients:
        fresh = fresh_ingredient_id(
            getattr(line, "food_reference_id", None), getattr(line, "display_name", "")
        )
        if fresh is not None:
            ids.add(fresh)
    return frozenset(ids)


def _best_serving_fit(meal: CatalogMeal, brief: SlotBrief) -> float:
    """How close this dish can get to the slot's calories, at any serving."""
    if brief.kcal_target <= 0:
        return 0.0
    options = _options(
        meal,
        takes_rice=meal.id in brief.takes_rice,
        cap_kcal=MEAL_KCAL_SHARE_CAP
        * brief.kcal_target
        / max(_SLOT_CALORIE_SHARES[brief.slot_index], 1e-9)
        if brief.slot_index < len(_SLOT_CALORIE_SHARES)
        else float("inf"),
    )
    gaps = [
        abs(serving[0] - brief.kcal_target) / brief.kcal_target
        for _portion, _steps, serving in options
    ]
    return max(0.0, 1.0 - min(gaps) / _FIT_TOLERANCE)


def _overlap(ingredients: frozenset[int], against: frozenset[int]) -> float:
    if not ingredients or not against:
        return 0.0
    return len(ingredients & against) / len(ingredients)


def rank_reference_dishes(
    meals: Iterable[CatalogMeal],
    brief: SlotBrief,
    *,
    embedding_scores: Mapping[str, float] | None = None,
    limit: int = 10,
) -> tuple[ScoredDish, ...]:
    """Shortlist the dishes most worth considering for a slot, best first.

    ``embedding_scores`` maps a recipe id to a similarity in 0..1 against the
    user's request or taste. Leave it out and the remaining legs share its
    weight, so scores stay on the same scale either way.
    """
    scores = embedding_scores or {}
    semantic_weight = _SEMANTIC_WEIGHT if scores else 0.0
    spare = _SEMANTIC_WEIGHT - semantic_weight
    weighted = _FIT_WEIGHT + _BASKET_WEIGHT + _TASTE_WEIGHT
    fit_weight = _FIT_WEIGHT + spare * _FIT_WEIGHT / weighted
    basket_weight = _BASKET_WEIGHT + spare * _BASKET_WEIGHT / weighted
    taste_weight = _TASTE_WEIGHT + spare * _TASTE_WEIGHT / weighted

    ranked: list[ScoredDish] = []
    for meal in meals:
        if meal.id in brief.exclude_recipe_ids:
            continue
        if (
            brief.cuisine
            and brief.cuisine.casefold() not in (meal.cuisine or "").casefold()
        ):
            continue
        fresh = dish_fresh_ingredient_ids(meal)
        fit = _best_serving_fit(meal, brief)
        basket = _overlap(fresh, brief.basket)
        taste = _overlap(fresh, brief.liked_ingredients)
        semantic = max(0.0, min(1.0, float(scores.get(meal.id, 0.0))))
        ranked.append(
            ScoredDish(
                meal=meal,
                score=(
                    fit_weight * fit
                    + basket_weight * basket
                    + taste_weight * taste
                    + semantic_weight * semantic
                ),
                fit=fit,
                basket=basket,
                taste=taste,
                semantic=semantic,
            )
        )
    ranked.sort(key=lambda dish: (-round(dish.score, 9), dish.meal.id))
    return tuple(ranked[:limit])

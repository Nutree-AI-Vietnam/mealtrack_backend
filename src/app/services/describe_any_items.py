"""Split the + panel's sentence by kind and turn its food into a meal to save.

The model reply is cleaned here, never trusted: drinks and activities go
through the water and activity normalisers, supplements are trimmed and
clamped, and protein powders, gainers and meal shakes join the food. Kinds
the caller did not ask for, and supplements while they are switched off, are
reported as skipped instead of saved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any

from src.app.commands.meal.create_manual_meal_command import (
    CustomNutrition,
    ManualMealItem,
)
from src.app.services.describe_hydration_items import DescribedDrink, normalize_drinks
from src.app.services.describe_log_parser import (
    DESCRIBE_MAX_ITEMS,
    clamp,
    finite,
    short_name,
)
from src.app.services.describe_movement_items import (
    DescribedActivity,
    normalize_activities,
)
from src.domain.model.supplement import LabelRow
from src.domain.model.supplement.describe_estimate import estimate_row
from src.domain.model.supplement.label_scan import FOOD_KINDS

FOOD, DRINK, ACTIVITY, SUPPLEMENT = "food", "drink", "activity", "supplement"
ALL_KINDS = frozenset({FOOD, DRINK, ACTIVITY, SUPPLEMENT})
NOT_REQUESTED = "not_requested"
SUPPLEMENTS_UNAVAILABLE = "supplements_unavailable"

FOOD_TEXT_MAX_CHARS = 300
WORDS_MAX_CHARS = 120
UNITS_RANGE = (0.25, 20.0)
MAX_ESTIMATE_ROWS = 25
# No label prints more of anything (50,000 IU of D3 is a weekly dose); this
# also keeps a unit conversion of the guess finite.
MAX_ESTIMATE_AMOUNT = 100_000.0
DISH_NAME_MAX_CHARS = 200
DEFAULT_FORM = "tablet"


@dataclass(frozen=True)
class DescribedSupplement:
    name: str
    words: str  # this item's words in the sentence, amount included
    units: float | None  # None logs the list's dose, or one unit
    list_ref: str | None
    catalog_query: str | None
    form: str = DEFAULT_FORM
    rows: tuple[LabelRow, ...] = ()  # the guessed unit, for an unknown product


@dataclass
class SentencePlan:
    food_text: str | None = None
    drinks: list[DescribedDrink] = field(default_factory=list)
    activities: list[DescribedActivity] = field(default_factory=list)
    supplements: list[DescribedSupplement] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    dropped: int = 0

    def add_food(self, words: str) -> None:
        """Add words to the food text once: the model may already have put a
        protein powder there as well as in the supplements."""
        if words.casefold() in (self.food_text or "").casefold():
            return
        text = ", ".join(part for part in (self.food_text, words) if part)
        self.food_text = text[:FOOD_TEXT_MAX_CHARS].strip() or None

    def skip(self, kind: str, title: str, reason: str) -> None:
        self.skipped.append({"kind": kind, "title": title, "reason": reason})


def plan_sentence(
    parse: dict[str, Any], kinds: frozenset[str], supplements_on: bool
) -> SentencePlan:
    """What to save from the model's split, in sentence order per kind."""
    plan = SentencePlan()
    food = _words(parse.get("food"), FOOD_TEXT_MAX_CHARS)
    if food:
        if FOOD in kinds:
            plan.add_food(food)
        else:
            plan.skip(FOOD, food, NOT_REQUESTED)

    drinks = normalize_drinks(parse.get("drinks") or [])
    plan.dropped += drinks.dropped + drinks.overflow
    if DRINK in kinds:
        plan.drinks = drinks.drinks
    else:
        for drink in drinks.drinks:
            plan.skip(DRINK, drink.name, NOT_REQUESTED)

    activities = normalize_activities(parse.get("activities") or [])
    plan.dropped += activities.dropped + activities.overflow
    if ACTIVITY in kinds:
        plan.activities = activities.activities
    else:
        for activity in activities.activities:
            plan.skip(ACTIVITY, activity.name, NOT_REQUESTED)

    supplements = parse.get("supplements") or []
    for item in supplements[: DESCRIBE_MAX_ITEMS * 2]:
        _plan_supplement(plan, item, kinds, supplements_on)
    plan.dropped += max(0, len(supplements) - DESCRIBE_MAX_ITEMS * 2)
    plan.dropped += max(0, len(plan.supplements) - DESCRIBE_MAX_ITEMS)
    plan.supplements = plan.supplements[:DESCRIBE_MAX_ITEMS]
    return plan


def _plan_supplement(
    plan: SentencePlan, item: dict[str, Any], kinds: frozenset[str], on: bool
) -> None:
    supplement = _supplement(item)
    if supplement is None:
        plan.dropped += 1
    elif item.get("product_kind") in FOOD_KINDS:
        # Protein powders, gainers and shakes carry calories: they log as food.
        if FOOD in kinds:
            plan.add_food(supplement.words)
        else:
            plan.skip(FOOD, supplement.name, NOT_REQUESTED)
    elif SUPPLEMENT not in kinds:
        plan.skip(SUPPLEMENT, supplement.name, NOT_REQUESTED)
    elif not on:
        plan.skip(SUPPLEMENT, supplement.name, SUPPLEMENTS_UNAVAILABLE)
    else:
        plan.supplements.append(supplement)


def _supplement(item: dict[str, Any]) -> DescribedSupplement | None:
    name = short_name(item.get("name"))
    if not name:
        return None
    units = finite(item.get("units"))
    estimate = item.get("estimate") or {}
    rows = []
    for nutrient in (estimate.get("nutrients") or [])[:MAX_ESTIMATE_ROWS]:
        amount = finite(nutrient.get("amount"))
        unit = short_name(nutrient.get("unit"))
        if amount is not None and 0 <= amount <= MAX_ESTIMATE_AMOUNT and unit:
            rows.append(estimate_row(nutrient["nutrient"], amount, unit))
    return DescribedSupplement(
        name=name,
        words=_words(item.get("words"), WORDS_MAX_CHARS) or name,
        units=None if units is None or units <= 0 else clamp(units, *UNITS_RANGE),
        list_ref=short_name(item.get("list_ref")) or None,
        catalog_query=short_name(item.get("catalog_query")) or None,
        form=estimate.get("form") or DEFAULT_FORM,
        rows=tuple(rows),
    )


def _words(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


# --- food: the parse-text reply as the app would save it --------------------


def meal_items(parsed_items: list[Any]) -> list[ManualMealItem]:
    """Parse-text items as prepared v2 save items, the way the app maps them."""
    return [_meal_item(item) for item in parsed_items]


def _meal_item(item: Any) -> ManualMealItem:
    origin = _origin(item)
    return ManualMealItem(
        name=item.name,
        quantity=item.quantity,
        unit=item.unit,
        allowed_units=item.allowed_units,
        origin=origin,
        fdc_id=item.fdc_id if origin == "usda" else None,
        food_reference_id=item.food_reference_id if origin == "local" else None,
        source_namespace=item.source_namespace if origin == "provider" else None,
        source_food_id=item.source_food_id if origin == "provider" else None,
        source_kind=origin,
        nutrition_contract_version="2",
        custom_nutrition=CustomNutrition(
            calories_per_100g=item.calories_per_100g or 0.0,
            protein_per_100g=item.protein_per_100g or 0.0,
            carbs_per_100g=item.carbs_per_100g or 0.0,
            fat_per_100g=item.fat_per_100g or 0.0,
            fiber_per_100g=item.fiber_per_100g or 0.0,
            sugar_per_100g=item.sugar_per_100g or 0.0,
        ),
        source_snapshot=item.source_snapshot,
    )


def _origin(item: Any) -> str:
    if item.food_reference_id is not None:
        return "local"
    if item.origin in ("local", "usda", "provider", "custom"):
        return item.origin
    if item.fdc_id is not None:
        return "usda"
    if item.source_food_id is not None or item.source_namespace is not None:
        return "provider"
    return "custom"


def dish_name(parsed_items: list[Any]) -> str:
    """The meal's title: its foods' names, as the timeline shows them."""
    names = ", ".join(item.name for item in parsed_items if item.name)
    return names[:DISH_NAME_MAX_CHARS].strip() or "Bữa ăn"


def meal_type_at(local_time: time) -> str:
    """The app's own rule for a meal logged at this local time."""
    hour = local_time.hour
    if 5 <= hour < 10:
        return "breakfast"
    if 10 <= hour < 14:
        return "lunch"
    if 14 <= hour < 17:
        return "snack"
    if 17 <= hour < 22:
        return "dinner"
    return "snack"

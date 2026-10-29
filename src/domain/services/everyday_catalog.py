"""Plan a small daily batch of everyday meals and shape them for catalog import.

The batch is the only input a person chooses. Dish names, ingredient lists, and
the importer schema are produced by code.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

EVERYDAY_CATALOG_SOURCE = "everyday-catalog"
EVERYDAY_CATALOG_ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
DEFAULT_BATCH_SIZE = 8
MIN_BATCH_SIZE = 5
MAX_BATCH_SIZE = 10
MAX_ATTEMPTS_PER_MEAL = 3

_MARKETS = ("VN", "US")
_MEAL_TYPES = ("breakfast", "lunch", "dinner")
_FAMILIES: dict[str, dict[str, tuple[str, ...]]] = {
    "VN": {
        "breakfast": ("egg_plate", "porridge", "sticky_rice", "bread", "noodle_soup"),
        "lunch": (
            "rice_plate",
            "noodle_soup",
            "noodle_stir",
            "grilled",
            "braised",
            "soup",
            "fresh_salad",
        ),
        "dinner": (
            "rice_plate",
            "noodle_soup",
            "noodle_stir",
            "grilled",
            "braised",
            "soup",
            "fresh_salad",
        ),
    },
    "US": {
        "breakfast": ("eggs", "oatmeal", "toast", "yogurt", "breakfast_sandwich"),
        "lunch": (
            "sandwich",
            "salad",
            "grain_bowl",
            "pasta",
            "grilled_plate",
            "soup_stew",
            "taco",
        ),
        "dinner": (
            "sandwich",
            "salad",
            "grain_bowl",
            "pasta",
            "grilled_plate",
            "soup_stew",
            "taco",
        ),
    },
}
_CUISINE = {"VN": "vietnamese", "US": "american"}
_LANGUAGE = {"VN": "vi", "US": "en"}
_COUNTRY_NAME = {"VN": "Vietnam", "US": "the United States"}
_UNITS = {
    "g": "g",
    "gram": "g",
    "grams": "g",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
}
_PROTEIN_WORDS = (
    "chicken",
    "beef",
    "pork",
    "fish",
    "shrimp",
    "egg",
    "tofu",
    "turkey",
    "salmon",
    "tuna",
)
_PRODUCE_WORDS = (
    "lettuce",
    "tomato",
    "onion",
    "garlic",
    "herb",
    "vegetable",
    "spinach",
    "cabbage",
    "cucumber",
    "carrot",
    "lime",
    "lemon",
    "chili",
    "pepper",
    "scallion",
    "bean sprout",
)


@dataclass(frozen=True)
class EverydaySlot:
    """One meal the daily job should find."""

    market: str
    cuisine: str
    meal_type: str
    dish_family: str
    language: str
    country_name: str


@dataclass(frozen=True)
class EverydayMealDraft:
    """A model-found meal, already checked for import shape."""

    name: str
    cuisine: str
    meal_type: str
    dish_family: str
    ingredients: tuple[tuple[str, float, str, str], ...]
    steps: tuple[str, ...]
    prep_time_minutes: int

    @property
    def recipe_key(self) -> str:
        return everyday_recipe_key(self.cuisine, self.name)


def clamp_batch_size(count: int) -> int:
    """Keep a daily run inside 5 to 10 meals."""

    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("batch size must be an integer")
    return min(MAX_BATCH_SIZE, max(MIN_BATCH_SIZE, count))


def local_day(moment: datetime) -> date:
    """Return the Saigon calendar day used for the daily cap."""

    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    return moment.astimezone(EVERYDAY_CATALOG_ZONE).date()


def day_start(day: date) -> datetime:
    """Return the Saigon midnight that opens a catalog day."""

    return datetime(day.year, day.month, day.day, tzinfo=EVERYDAY_CATALOG_ZONE)


def plan_slots(
    day: date, count: int, *, market: str | None = None
) -> tuple[EverydaySlot, ...]:
    """Choose which country, meal time, and dish family to ask for.

    Countries alternate through the batch. Families rotate by calendar day so
    a repeated run does not ask for the same shape every morning.
    """

    batch = clamp_batch_size(count)
    markets = (market,) if market is not None else _MARKETS
    if any(item not in _MARKETS for item in markets):
        raise ValueError("market must be VN or US")
    slots: list[EverydaySlot] = []
    for index in range(batch):
        chosen = markets[index % len(markets)]
        meal_type = _MEAL_TYPES[index % len(_MEAL_TYPES)]
        families = _FAMILIES[chosen][meal_type]
        family = families[(day.toordinal() + index) % len(families)]
        slots.append(
            EverydaySlot(
                market=chosen,
                cuisine=_CUISINE[chosen],
                meal_type=meal_type,
                dish_family=family,
                language=_LANGUAGE[chosen],
                country_name=_COUNTRY_NAME[chosen],
            )
        )
    return tuple(slots)


def build_everyday_prompt(
    slot: EverydaySlot, *, exclude_names: tuple[str, ...]
) -> tuple[str, str]:
    """Tell the model to find one everyday dish. The caller owns the schema."""

    excluded = ", ".join(exclude_names[:20])
    avoid = f"\nDo not repeat these dishes: {excluded}." if excluded else ""
    name_rule = (
        "Use the Vietnamese dish name."
        if slot.language == "vi"
        else "Use the English dish name."
    )
    system = (
        "You find one everyday home-cooked meal that people in "
        f"{slot.country_name} actually eat. Return JSON only."
    )
    prompt = f"""Find one {slot.meal_type} in the {slot.dish_family} family from {slot.country_name}.
{name_rule}
Ingredient names must be English. Quantities must use g or ml only.
3 to 8 ingredients. 2 to 6 short steps. prep_time_minutes is the total time.
Do not include calories, macros, or a food database id.{avoid}

Return JSON:
{{
  "name": "dish name",
  "ingredients": [{{"name": "chicken breast", "amount": 120, "unit": "g"}}],
  "recipe_steps": [{{"step": 1, "instruction": "Cook the chicken.", "duration_minutes": 10}}],
  "prep_time_minutes": 20
}}"""
    return system, prompt


def draft_from_payload(slot: EverydaySlot, payload: object) -> EverydayMealDraft | None:
    """Accept a model payload only when it can be imported without editing."""

    if not isinstance(payload, dict):
        return None
    name = _text(payload.get("name"))
    if name is None or len(name) > 120:
        return None
    ingredients = _ingredients(payload.get("ingredients"))
    if ingredients is None:
        return None
    steps = _steps(payload.get("recipe_steps"))
    if steps is None:
        return None
    prep = payload.get("prep_time_minutes")
    if isinstance(prep, bool) or not isinstance(prep, int) or not 1 <= prep <= 120:
        return None
    return EverydayMealDraft(
        name=name,
        cuisine=slot.cuisine,
        meal_type=slot.meal_type,
        dish_family=slot.dish_family,
        ingredients=ingredients,
        steps=steps,
        prep_time_minutes=prep,
    )


def build_import_manifest(draft: EverydayMealDraft) -> dict:
    """Build the one-recipe manifest the catalog importer already accepts."""

    recipe = {
        "recipe_key": draft.recipe_key,
        "cuisine": draft.cuisine,
        "name": draft.name,
        "description": f"Everyday {draft.cuisine} {draft.meal_type}.",
        "meal_types": [draft.meal_type],
        "source_name": EVERYDAY_CATALOG_SOURCE,
        "prep_time_minutes": draft.prep_time_minutes,
        "cook_time_minutes": 0,
        "tag": f"family:{draft.dish_family}",
        "ingredients": [
            {
                "food_reference_id": None,
                "name": name,
                "quantity": quantity,
                "unit": unit,
                "category": category,
            }
            for name, quantity, unit, category in draft.ingredients
        ],
    }
    if draft.steps:
        recipe["steps"] = [
            {
                "step_number": index,
                "title": step[:80],
                "description": step,
            }
            for index, step in enumerate(draft.steps, start=1)
        ]
    return {
        "release_key": f"everyday-{draft.cuisine}",
        "expected_recipe_count": 1,
        "recipes": [recipe],
    }


def everyday_recipe_key(cuisine: str, name: str) -> str:
    slug = _slug(name)
    return f"everyday-{cuisine}-{slug}"[:160]


def _slug(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(
        character for character in folded if not unicodedata.combining(character)
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.casefold()).strip("-")
    return slug or "meal"


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _ingredients(
    value: object,
) -> tuple[tuple[str, float, str, str], ...] | None:
    if not isinstance(value, list) or not 3 <= len(value) <= 8:
        return None
    rows: list[tuple[str, float, str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        name = _text(item.get("name"))
        unit = _UNITS.get(str(item.get("unit", "")).strip().casefold())
        amount = item.get("amount", item.get("quantity"))
        if name is None or unit is None or isinstance(amount, bool):
            return None
        if not isinstance(amount, int | float) or amount <= 0:
            return None
        rows.append((name, float(amount), unit, _category(name)))
    return tuple(rows)


def _steps(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not 2 <= len(value) <= 6:
        return None
    steps: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        instruction = _text(item.get("instruction") or item.get("description"))
        if instruction is None:
            return None
        steps.append(instruction)
    return tuple(steps)


def _category(name: str) -> str:
    folded = name.casefold()
    if any(word in folded for word in _PROTEIN_WORDS):
        return "protein"
    if any(word in folded for word in _PRODUCE_WORDS):
        return "produce"
    return "pantry"

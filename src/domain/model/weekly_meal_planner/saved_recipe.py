"""A copy of a saved recipe held by a weekly plan slot.

A slot that holds one of the user's saved recipes (an imported recipe or a
bookmarked AI meal idea) stores this snapshot when the recipe is put in the
slot. The plan summary, groceries and the diary log all read the snapshot,
so deleting the saved recipe later leaves the week as it is.

Ingredient amounts and their macros cover the whole recipe, which makes
``servings`` servings; the top-level macros are for one serving. Calories
are never taken from the saved payload: they come from the backend formula
over the per-serving macros, like every logged meal.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.domain.model.nutrition.macros import Macros

SAVED_RECIPE_SNAPSHOT_VERSION = 1
SOURCE_RECIPE_IMPORT = "recipe_import"
SOURCE_SUGGESTION = "suggestion"
_RECIPE_IMPORT_PREFIX = "recipe_import_"

MAX_SAVED_RECIPE_INGREDIENTS = 60
MAX_SAVED_RECIPE_STEPS = 40
MAX_SAVED_RECIPE_SERVINGS = 100
# Per serving; anything larger is not a meal.
_MAX_MACRO_GRAMS = 5_000.0
_MAX_MINUTES = 7 * 24 * 60
_MAX_NAME_CHARS = 255
_MAX_INGREDIENT_NAME_CHARS = 200
_MAX_UNIT_CHARS = 64
_MAX_TEXT_CHARS = 2_000
_MAX_LABEL_CHARS = 50
_MAX_EMOJI_CHARS = 8
_MAX_URL_CHARS = 2_048
_MACRO_KEYS = ("protein", "carbs", "fat", "fiber", "sugar")


@dataclass(frozen=True)
class SavedRecipeIngredient:
    """One recipe line; amount, grams and macros cover the whole recipe."""

    name: str
    amount: float | None = None
    unit: str = ""
    grams: float | None = None
    food_reference_id: int | None = None
    protein: float | None = None
    carbs: float | None = None
    fat: float | None = None
    fiber: float | None = None
    sugar: float | None = None
    optional: bool = False

    @property
    def has_macros(self) -> bool:
        return any(value is not None for value in (self.protein, self.carbs, self.fat))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "amount": self.amount,
            "unit": self.unit,
            "grams": self.grams,
            "food_reference_id": self.food_reference_id,
            "protein": self.protein,
            "carbs": self.carbs,
            "fat": self.fat,
            "fiber": self.fiber,
            "sugar": self.sugar,
            "optional": self.optional,
        }


@dataclass(frozen=True)
class SavedRecipeStep:
    instruction: str
    duration_minutes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "duration_minutes": self.duration_minutes,
        }


@dataclass(frozen=True)
class SavedRecipeSnapshot:
    """What a slot needs from a saved recipe, copied at write time."""

    suggestion_id: str
    name: str
    servings: int = 1
    protein: float = 0.0
    carbs: float = 0.0
    fat: float = 0.0
    fiber: float = 0.0
    sugar: float = 0.0
    ingredients: tuple[SavedRecipeIngredient, ...] = ()
    steps: tuple[SavedRecipeStep, ...] = ()
    image_url: str | None = None
    cook_time_minutes: int | None = None
    description: str | None = None
    emoji: str | None = None
    cuisine_type: str | None = None
    origin_country: str | None = None
    language: str | None = None
    source: str = SOURCE_SUGGESTION
    source_url: str | None = None
    servings_estimated: bool = False

    @property
    def calories(self) -> int:
        """Per-serving calories from the backend formula over the macros."""
        return int(
            round(
                Macros.raw_total_calories(
                    self.protein, self.carbs, self.fat, self.fiber
                )
            )
        )

    def macros(self, portion_multiplier: float = 1.0) -> Macros:
        return Macros(
            protein=self.protein * portion_multiplier,
            carbs=self.carbs * portion_multiplier,
            fat=self.fat * portion_multiplier,
            fiber=self.fiber * portion_multiplier,
            sugar=self.sugar * portion_multiplier,
        )

    @classmethod
    def from_saved_suggestion(
        cls, suggestion_id: str, suggestion_data: Mapping[str, Any] | None
    ) -> SavedRecipeSnapshot:
        """Build from a saved suggestion's ``suggestion_data``.

        Raises ValueError when the payload has no name or no per-serving
        protein, carbs or fat, so the slot could not show or log it.
        """
        data = suggestion_data if isinstance(suggestion_data, Mapping) else {}
        name = _text(
            _first(data, ("meal_name", "dish_name", "name", "title")), _MAX_NAME_CHARS
        )
        if not name:
            raise ValueError("a saved recipe needs a name")
        macros = _macros(data)
        if macros is None:
            raise ValueError("a saved recipe needs per-serving macros")
        raw_import = data.get("recipe_import")
        imported: Mapping[str, Any] = (
            raw_import if isinstance(raw_import, Mapping) else {}
        )
        is_import = bool(imported) or suggestion_id.startswith(_RECIPE_IMPORT_PREFIX)
        servings = _count(data.get("servings"), MAX_SAVED_RECIPE_SERVINGS) or 1
        return cls(
            suggestion_id=suggestion_id,
            name=name,
            servings=servings,
            ingredients=_ingredients(data),
            steps=_steps(data),
            image_url=_url(data.get("image_url")),
            # Imports keep the source's total time; AI meal ideas put the
            # total (cooking included) in prep_time_minutes.
            cook_time_minutes=_count(imported.get("total_time_minutes"), _MAX_MINUTES)
            or _count(
                _first(
                    data,
                    ("prep_time_minutes", "cook_time_minutes", "total_time_minutes"),
                ),
                _MAX_MINUTES,
            ),
            description=_text(
                _first(data, ("description", "summary")), _MAX_TEXT_CHARS
            ),
            emoji=_text(data.get("emoji"), _MAX_EMOJI_CHARS),
            cuisine_type=_text(data.get("cuisine_type"), _MAX_LABEL_CHARS),
            origin_country=_text(data.get("origin_country"), _MAX_LABEL_CHARS),
            language=_text(data.get("language"), 10),
            source=SOURCE_RECIPE_IMPORT if is_import else SOURCE_SUGGESTION,
            source_url=_url(imported.get("source_url")),
            servings_estimated=bool(imported.get("servings_estimated")),
            protein=macros["protein"],
            carbs=macros["carbs"],
            fat=macros["fat"],
            fiber=macros["fiber"],
            sugar=macros["sugar"],
        )

    @classmethod
    def from_dict(
        cls, stored: Mapping[str, Any] | None, *, suggestion_id: str | None = None
    ) -> SavedRecipeSnapshot:
        """Read a stored snapshot. Lenient: a slot must always render."""
        data = stored if isinstance(stored, Mapping) else {}
        raw_macros = data.get("macros_per_serving")
        macros = raw_macros if isinstance(raw_macros, Mapping) else {}
        return cls(
            suggestion_id=str(suggestion_id or data.get("suggestion_id") or ""),
            name=_text(data.get("name"), _MAX_NAME_CHARS) or "",
            servings=_count(data.get("servings"), MAX_SAVED_RECIPE_SERVINGS) or 1,
            ingredients=_ingredients({"ingredients": data.get("ingredients")}),
            steps=_steps({"steps": data.get("steps")}),
            image_url=_url(data.get("image_url")),
            cook_time_minutes=_count(data.get("cook_time_minutes"), _MAX_MINUTES),
            description=_text(data.get("description"), _MAX_TEXT_CHARS),
            emoji=_text(data.get("emoji"), _MAX_EMOJI_CHARS),
            cuisine_type=_text(data.get("cuisine_type"), _MAX_LABEL_CHARS),
            origin_country=_text(data.get("origin_country"), _MAX_LABEL_CHARS),
            language=_text(data.get("language"), 10),
            source=str(data.get("source") or SOURCE_SUGGESTION),
            source_url=_url(data.get("source_url")),
            servings_estimated=bool(data.get("servings_estimated")),
            protein=_grams(macros.get("protein")) or 0.0,
            carbs=_grams(macros.get("carbs")) or 0.0,
            fat=_grams(macros.get("fat")) or 0.0,
            fiber=_grams(macros.get("fiber")) or 0.0,
            sugar=_grams(macros.get("sugar")) or 0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SAVED_RECIPE_SNAPSHOT_VERSION,
            "suggestion_id": self.suggestion_id,
            "name": self.name,
            "servings": self.servings,
            "macros_per_serving": {
                "protein": self.protein,
                "carbs": self.carbs,
                "fat": self.fat,
                "fiber": self.fiber,
                "sugar": self.sugar,
            },
            "ingredients": [item.to_dict() for item in self.ingredients],
            "steps": [step.to_dict() for step in self.steps],
            "image_url": self.image_url,
            "cook_time_minutes": self.cook_time_minutes,
            "description": self.description,
            "emoji": self.emoji,
            "cuisine_type": self.cuisine_type,
            "origin_country": self.origin_country,
            "language": self.language,
            "source": self.source,
            "source_url": self.source_url,
            "servings_estimated": self.servings_estimated,
        }


def _first(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _text(value: Any, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:max_chars] or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _grams(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0 or number > _MAX_MACRO_GRAMS * 100:
        return None
    return number


def _count(value: Any, maximum: int) -> int | None:
    number = _number(value)
    if number is None or number < 1:
        return None
    return min(int(round(number)), maximum)


def _url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if len(url) > _MAX_URL_CHARS or not url.lower().startswith(("https://", "http://")):
        return None
    return url


def _macros(data: Mapping[str, Any]) -> dict[str, float] | None:
    nested = data.get("macros")
    nested = nested if isinstance(nested, Mapping) else {}
    values: dict[str, float | None] = {}
    for key in _MACRO_KEYS:
        raw = _first(nested, (key, f"{key}_g"))
        if raw is None:
            raw = _first(data, (key, f"{key}_g"))
        number = _number(raw)
        if number is not None and (number < 0 or number > _MAX_MACRO_GRAMS):
            return None
        values[key] = number
    if all(values[key] is None for key in ("protein", "carbs", "fat")):
        return None
    return {key: value or 0.0 for key, value in values.items()}


def _ingredients(data: Mapping[str, Any]) -> tuple[SavedRecipeIngredient, ...]:
    raw_items = _first(data, ("ingredients", "items", "food_items"))
    if not isinstance(raw_items, list):
        return ()
    items: list[SavedRecipeIngredient] = []
    for raw in raw_items:
        if len(items) == MAX_SAVED_RECIPE_INGREDIENTS:
            break
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, Mapping):
            continue
        name = _text(
            _first(raw, ("name", "ingredient", "food")), _MAX_INGREDIENT_NAME_CHARS
        )
        if not name:
            continue
        amount = _number(_first(raw, ("quantity", "amount")))
        food_reference_id = _number(raw.get("food_reference_id"))
        items.append(
            SavedRecipeIngredient(
                name=name,
                amount=amount if amount is not None and amount > 0 else None,
                unit=_text(_first(raw, ("unit", "measure")), _MAX_UNIT_CHARS) or "",
                grams=_positive(raw.get("grams")),
                food_reference_id=(
                    int(food_reference_id)
                    if food_reference_id is not None
                    and food_reference_id >= 1
                    and food_reference_id == int(food_reference_id)
                    and food_reference_id < 2**31
                    else None
                ),
                protein=_grams(raw.get("protein")),
                carbs=_grams(raw.get("carbs")),
                fat=_grams(raw.get("fat")),
                fiber=_grams(raw.get("fiber")),
                sugar=_grams(raw.get("sugar")),
                optional=raw.get("optional") is True,
            )
        )
    return tuple(items)


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _steps(data: Mapping[str, Any]) -> tuple[SavedRecipeStep, ...]:
    raw_steps = _first(
        data, ("recipe_steps", "instructions", "steps", "cooking_instructions")
    )
    if not isinstance(raw_steps, list):
        return ()
    steps: list[SavedRecipeStep] = []
    for raw in raw_steps:
        if len(steps) == MAX_SAVED_RECIPE_STEPS:
            break
        if isinstance(raw, str):
            raw = {"instruction": raw}
        if not isinstance(raw, Mapping):
            continue
        instruction = _text(
            _first(raw, ("instruction", "text", "description")), _MAX_TEXT_CHARS
        )
        if not instruction:
            continue
        steps.append(
            SavedRecipeStep(
                instruction=instruction,
                duration_minutes=_count(
                    _first(raw, ("duration_minutes", "minutes")), _MAX_MINUTES
                ),
            )
        )
    return tuple(steps)

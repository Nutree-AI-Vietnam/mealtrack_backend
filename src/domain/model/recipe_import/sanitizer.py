"""Turn untrusted recipe fields (AI output or app edits) into domain values.

Limits are applied here instead of in the AI schema: the provider drops
length and range constraints from strict schemas, so an over-long answer
must be trimmed, not rejected.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.model.common.enums import MealType
from src.domain.model.recipe_import.extracted_recipe import (
    ExtractedIngredient,
    ExtractedRecipe,
    RecipeStep,
)
from src.domain.model.recipe_import.limits import (
    MAX_DESCRIPTION_CHARS,
    MAX_EMOJI_CHARS,
    MAX_GROUP_CHARS,
    MAX_INGREDIENT_GRAMS,
    MAX_INGREDIENT_NAME_CHARS,
    MAX_INGREDIENT_QUANTITY,
    MAX_INGREDIENTS,
    MAX_LABEL_CHARS,
    MAX_MINUTES,
    MAX_NAME_CHARS,
    MAX_NOTE_CHARS,
    MAX_STEP_CHARS,
    MAX_STEP_MINUTES,
    MAX_STEPS,
    MAX_UNIT_CHARS,
)
from src.domain.model.recipe_import.portions import eaten_share
from src.domain.model.recipe_import.text import clamp_servings, clean_text

_MEAL_TYPES = {meal_type.value: meal_type for meal_type in MealType}


def sanitize_extracted_recipe(
    raw: Mapping[str, Any], *, language: str
) -> ExtractedRecipe:
    name = clean_text(raw.get("name"), max_chars=MAX_NAME_CHARS) or ""
    ingredients = sanitize_ingredients(raw.get("ingredients"))
    steps = sanitize_steps(raw.get("steps"))
    return ExtractedRecipe(
        is_recipe=bool(raw.get("is_recipe")) and bool(name),
        name=name,
        language=language,
        rejection_reason=clean_text(raw.get("rejection_reason"), max_chars=300),
        english_name=clean_text(raw.get("english_name"), max_chars=MAX_NAME_CHARS),
        description=clean_text(raw.get("description"), max_chars=MAX_DESCRIPTION_CHARS),
        servings=clamp_servings(_int(raw.get("servings"))),
        servings_stated=bool(raw.get("servings_stated")),
        makes=clean_text(raw.get("makes"), max_chars=MAX_LABEL_CHARS),
        quantities_stated=bool(raw.get("quantities_stated")),
        prep_time_minutes=_minutes(raw.get("prep_time_minutes"), MAX_MINUTES),
        cook_time_minutes=_minutes(raw.get("cook_time_minutes"), MAX_MINUTES),
        total_time_minutes=_minutes(raw.get("total_time_minutes"), MAX_MINUTES),
        ingredients=ingredients,
        steps=steps,
        cuisine_type=clean_text(raw.get("cuisine_type"), max_chars=MAX_LABEL_CHARS),
        origin_country=clean_text(raw.get("origin_country"), max_chars=MAX_LABEL_CHARS),
        meal_type=_MEAL_TYPES.get(str(raw.get("meal_type") or "").lower()),
        emoji=clean_text(raw.get("emoji"), max_chars=MAX_EMOJI_CHARS),
    )


def sanitize_ingredients(raw: Any) -> tuple[ExtractedIngredient, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    items: list[ExtractedIngredient] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        name = clean_text(entry.get("name"), max_chars=MAX_INGREDIENT_NAME_CHARS)
        if not name:
            continue
        english = clean_text(
            entry.get("english_name"), max_chars=MAX_INGREDIENT_NAME_CHARS
        )
        quantity = _positive_float(entry.get("quantity"), MAX_INGREDIENT_QUANTITY)
        unit = clean_text(entry.get("unit"), max_chars=MAX_UNIT_CHARS)
        grams = _positive_float(entry.get("grams"), MAX_INGREDIENT_GRAMS)
        if quantity is None and grams is not None:
            # A weight with no count reads as grams ("300 g pork").
            quantity, unit = grams, "g"
        items.append(
            ExtractedIngredient(
                name=name,
                english_name=english or name,
                quantity=quantity,
                unit=unit,
                grams=grams,
                note=clean_text(entry.get("note"), max_chars=MAX_NOTE_CHARS),
                optional=bool(entry.get("optional")),
                group=clean_text(entry.get("group"), max_chars=MAX_GROUP_CHARS),
                amount_estimated=bool(entry.get("amount_estimated")),
                eaten_share=eaten_share(entry.get("eaten_share")),
            )
        )
        if len(items) >= MAX_INGREDIENTS:
            break
    return tuple(items)


def sanitize_steps(raw: Any) -> tuple[RecipeStep, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    steps: list[RecipeStep] = []
    for entry in raw:
        text: Any
        minutes: Any
        if isinstance(entry, str):
            text, minutes = entry, None
        elif isinstance(entry, Mapping):
            text = entry.get("instruction") or entry.get("text")
            minutes = entry.get("duration_minutes")
        else:
            continue
        instruction = clean_text(text, max_chars=MAX_STEP_CHARS)
        if not instruction:
            continue
        steps.append(
            RecipeStep(
                instruction=instruction,
                duration_minutes=_minutes(minutes, MAX_STEP_MINUTES),
            )
        )
        if len(steps) >= MAX_STEPS:
            break
    return tuple(steps)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return int(round(value))


def _minutes(value: Any, maximum: int) -> int | None:
    minutes = _int(value)
    if minutes is None or minutes <= 0 or minutes > maximum:
        return None
    return minutes


def _positive_float(value: Any, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > maximum:
        return None
    return round(number, 3)

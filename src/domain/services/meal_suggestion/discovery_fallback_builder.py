"""Deterministic fallback meals for discovery when AI generation is unavailable."""

import re
import uuid
from itertools import cycle
from typing import Iterable

from src.domain.model.meal_suggestion import SuggestionSession
from src.domain.services.meal_suggestion.macro_validation_service import (
    MacroValidationService,
)

DEFAULT_INGREDIENTS = {
    "breakfast": ["Egg", "Oat", "Greek Yogurt", "Tofu"],
    "lunch": ["Chicken", "Rice", "Bean", "Tofu"],
    "dinner": ["Chicken", "Rice", "Salmon", "Lentil"],
    "snack": ["Greek Yogurt", "Fruit", "Cottage Cheese", "Hummus"],
}

NAME_PATTERNS = (
    "{main} {side} Bowl",
    "{main} {side} Plate",
    "{accent} {main} Skillet",
    "{main} {side} Salad",
    "{main} {side} Wrap",
    "{accent} {main} Soup",
    "{main} {side} Stir Fry",
    "{accent} {main} Tray Bake",
    "{main} {side} Grain Bowl",
    "{accent} {main} Rice Bowl",
    "{main} {side} Meal Prep",
    "{accent} {main} Dinner Plate",
)

ACCENTS = (
    "Herbed",
    "Citrus",
    "Garden",
    "Savory",
    "Roasted",
    "Grilled",
    "Mediterranean",
    "Coconut",
    "Sesame",
    "Tomato",
    "Ginger",
    "Lemon",
)

MACRO_SPLITS = (
    (0.30, 0.40, 0.30),
    (0.34, 0.36, 0.30),
    (0.28, 0.44, 0.28),
    (0.32, 0.38, 0.30),
    (0.26, 0.46, 0.28),
)


def build_discovery_fallback_meals(
    session: SuggestionSession,
    exclude_meal_names: Iterable[str],
    count: int,
    macro_validator: MacroValidationService,
) -> list[dict]:
    """Build valid discovery meals without an AI call."""

    if count <= 0:
        return []

    excluded = {name.strip().lower() for name in exclude_meal_names if name}
    ingredients = _fallback_ingredients(session)
    accents = _fallback_accents(session)
    meals: list[dict] = []

    for index, name in enumerate(_candidate_names(ingredients, accents)):
        normalized = name.lower()
        if normalized in excluded:
            continue

        excluded.add(normalized)
        macros = _fallback_macros(session, index, macro_validator)
        meals.append(
            {
                "id": f"disc_{uuid.uuid4().hex[:12]}",
                "name": name,
                "english_name": name,
                "calories": macros["calories"],
                "protein": macros["protein"],
                "carbs": macros["carbs"],
                "fat": macros["fat"],
                "nutrition_status": "estimated",
            }
        )
        if len(meals) >= count:
            break

    while len(meals) < count:
        index = len(meals)
        name = f"Balanced {session.meal_type.title()} Option {index + 1}"
        normalized = name.lower()
        if normalized in excluded:
            name = f"Balanced Meal Option {uuid.uuid4().hex[:4]}"
        excluded.add(name.lower())
        macros = _fallback_macros(session, index, macro_validator)
        meals.append(
            {
                "id": f"disc_{uuid.uuid4().hex[:12]}",
                "name": name,
                "english_name": name,
                "calories": macros["calories"],
                "protein": macros["protein"],
                "carbs": macros["carbs"],
                "fat": macros["fat"],
                "nutrition_status": "estimated",
            }
        )

    return meals


def _fallback_ingredients(session: SuggestionSession) -> list[str]:
    cleaned = [_clean_ingredient_name(raw) for raw in session.ingredients[:6]]
    ingredients = [name for name in cleaned if name]
    if ingredients:
        return ingredients
    return DEFAULT_INGREDIENTS.get(
        session.meal_type, DEFAULT_INGREDIENTS["lunch"]
    ).copy()


def _fallback_accents(session: SuggestionSession) -> list[str]:
    cuisine = _clean_ingredient_name(getattr(session, "cuisine_region", "") or "")
    if cuisine:
        return [cuisine, *ACCENTS]
    return list(ACCENTS)


def _candidate_names(ingredients: list[str], accents: list[str]) -> Iterable[str]:
    max_candidates = max(96, len(ingredients) * len(accents) * len(NAME_PATTERNS))
    for _, pattern, main, side, accent in zip(
        range(max_candidates),
        cycle(NAME_PATTERNS),
        cycle(ingredients),
        cycle(ingredients[1:] or ingredients),
        cycle(accents),
        strict=False,
    ):
        name = pattern.format(main=main, side=side, accent=accent)
        yield _limit_name(name)


def _fallback_macros(
    session: SuggestionSession,
    index: int,
    macro_validator: MacroValidationService,
) -> dict:
    target_calories = max(50.0, min(float(session.target_calories or 500), 3000.0))

    protein_target = getattr(session, "protein_target", None)
    carbs_target = getattr(session, "carbs_target", None)
    fat_target = getattr(session, "fat_target", None)
    if protein_target and carbs_target and fat_target:
        return macro_validator.validate_and_correct(
            {
                "calories": target_calories,
                "protein": protein_target,
                "carbs": carbs_target,
                "fat": fat_target,
            }
        )

    split = MACRO_SPLITS[index % len(MACRO_SPLITS)]
    variation = 1.0 + ((index % 5) - 2) * 0.04
    calories = max(50.0, min(target_calories * variation, 3000.0))
    protein_ratio, carbs_ratio, fat_ratio = split
    return macro_validator.validate_and_correct(
        {
            "calories": calories,
            "protein": round((calories * protein_ratio) / 4, 1),
            "carbs": round((calories * carbs_ratio) / 4, 1),
            "fat": round(max(3.0, (calories * fat_ratio) / 9), 1),
        }
    )


def _clean_ingredient_name(raw: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z ]*", raw or "")
    if not words:
        return ""
    cleaned = " ".join(" ".join(words).split())[:32]
    return cleaned.title()


def _limit_name(name: str) -> str:
    words = name.split()
    while len(" ".join(words)) > 60 and len(words) > 2:
        words.pop(-2)
    return " ".join(words)

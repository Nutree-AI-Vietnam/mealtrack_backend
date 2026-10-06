"""Rank catalog meals toward food people can cook and eat at that time of day."""

from __future__ import annotations

import unicodedata

# Familiar breakfast shapes. Luxury ingredients are rejected separately.
_BREAKFAST_FORMS = (
    "trung",
    "op la",
    "omelet",
    "omelette",
    "scrambled egg",
    "banh mi",
    "toast",
    "yen mach",
    "oatmeal",
    "oat",
    "chao",
    "xoi",
    "sua chua",
    "yogurt",
    "pancake",
    "banh cuon",
)
# Expensive or fussy food that should not lead a normal week.
_LUXURY = (
    "ca hoi",
    "salmon",
    "tom hum",
    "lobster",
    "bao ngu",
    "abalone",
    "wagyu",
    "bit tet",
    "steak",
    "pho mai",
    "cheese",
    "kieu nhat",
    "kieu phap",
    "kieu y",
    "sashimi",
    "sushi",
)
# A full plated dinner, not a breakfast.
_DINNER_PLATES = (
    "com",
    "nuong",
    "lau",
    "suon",
    "ap chao",
    "ca kho",
    "bo luc",
)
# Titles written as articles rather than a dish someone can follow.
_CLICKBAIT = (
    "be se",
    "be an",
    "bi quyet",
    "bo nao",
    "meo nau",
    "ngon tuyet",
    "an tron",
    "khong the cuong",
)


def practicality_rank(
    name: str,
    meal_type: str,
    *,
    total_minutes: int | None = None,
    ingredient_count: int | None = None,
    non_meal: bool = False,
) -> tuple[int, int]:
    """Return a sort key where a lower pair is an easier, better-fitting meal.

    The first number is how well the dish fits the meal time. The second is how
    ordinary the cooking is. Salmon porridge sorts behind eggs for breakfast,
    and a named request can still select it later.
    """
    if non_meal:
        return (3, 2)
    folded = _fold(name)
    luxury = _contains(folded, _LUXURY)
    clickbait = _contains(folded, _CLICKBAIT)
    if meal_type == "breakfast":
        breakfast_form = _contains(folded, _BREAKFAST_FORMS)
        dinner_plate = _contains(folded, _DINNER_PLATES)
        if luxury or (dinner_plate and not breakfast_form):
            occasion = 2
        elif breakfast_form:
            occasion = 0
        else:
            occasion = 1
    elif luxury:
        occasion = 1
    elif clickbait:
        occasion = 2
    else:
        occasion = 0
    if luxury or clickbait:
        simplicity = 2
    elif (total_minutes or 0) > 45 or (ingredient_count or 0) > 12:
        simplicity = 2
    elif (total_minutes is None or total_minutes <= 35) and (
        ingredient_count is None or ingredient_count <= 8
    ):
        simplicity = 0
    else:
        simplicity = 1
    return (occasion, simplicity)


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )


def _contains(folded: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {folded} "
    return any(f" {phrase} " in padded for phrase in phrases)

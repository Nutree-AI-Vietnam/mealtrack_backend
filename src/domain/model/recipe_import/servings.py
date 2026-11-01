"""How many people a recipe feeds, as its source states it.

``servings`` always counts people. A yield that counts pieces or an amount
("Makes 12", "24 cookies", "1 loaf") is what the recipe makes, not how many
it feeds; the portion pass then estimates the servings.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from src.domain.model.recipe_import.extracted_recipe import ExtractedRecipe
from src.domain.model.recipe_import.source_material import StructuredRecipe
from src.domain.model.recipe_import.text import parse_servings

# Words that make a yield a count of people or portions, in the languages
# recipe sites publish in.
_PEOPLE = re.compile(
    r"\b(?:serv(?:e|es|ing|ings)|portions?|people|persons?|pax|guests?|"
    r"người|phần|suất|porci(?:ón|on|ones)|raci(?:ón|on|ones)|personas?|"
    r"personnes?|parts?|portionen|personen|porzioni|persone|porções|pessoas|"
    r"porsi|orang|kişilik)\b|人分|人份|인분|نفر|أشخاص",
    re.IGNORECASE,
)
_BARE_COUNT = re.compile(r"^\s*\d{1,3}(?:\s*[-–]\s*\d{1,3})?\s*$")


def yield_servings(raw: Any) -> int | None:
    """People a schema.org ``recipeYield`` states, or None when it counts pieces.

    ``"Serves 4"`` and ``["4", "4 servings"]`` state 4; a bare number counts
    servings, as schema.org intends. ``"Makes 12"`` and ``["24", "24
    cookies"]`` count what the recipe makes.
    """
    values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    bare: int | None = None
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            bare = bare or parse_servings(value)
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if _PEOPLE.search(value):
            return parse_servings(value)
        if not _BARE_COUNT.match(value):
            return None
        bare = bare or parse_servings(value)
    return bare


def with_source_servings(
    recipe: ExtractedRecipe, structured: StructuredRecipe | None
) -> ExtractedRecipe:
    """Apply the page's own yield field, which says more than page prose.

    A yield that counts people is stated servings. A yield that counts
    pieces states none: the AI reading "Makes 12" as 12 servings is undone,
    so the portion pass estimates how many people 12 pancakes feed.
    """
    if structured is None:
        return recipe
    if structured.servings:
        return replace(recipe, servings=structured.servings, servings_stated=True)
    if not structured.recipe_yield:
        return recipe
    makes = recipe.makes or structured.recipe_yield
    pieces = parse_servings(structured.recipe_yield)
    if recipe.servings_stated and recipe.servings != pieces:
        # The page states servings elsewhere ("Makes 12, serves 4").
        return replace(recipe, makes=makes)
    return replace(
        recipe,
        servings=None if recipe.servings == pieces else recipe.servings,
        servings_stated=False,
        makes=makes,
    )

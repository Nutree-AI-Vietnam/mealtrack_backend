"""When an extracted recipe is good enough, and when to read further."""

from __future__ import annotations

import re

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.recipe_import.extracted_recipe import ExtractedRecipe
from src.domain.model.recipe_import.limits import (
    MIN_INGREDIENTS_FOR_CAPTION,
    MIN_QUANTIFIED_SHARE_FOR_CAPTION,
)
from src.domain.model.recipe_import.source_material import RecipeSourceMaterial


def is_cookable(recipe: ExtractedRecipe) -> bool:
    """A recipe with something to buy and something to do."""
    return recipe.is_recipe and bool(recipe.ingredients) and bool(recipe.steps)


def caption_is_enough(recipe: ExtractedRecipe) -> bool:
    """Whether a video's caption alone gave a usable recipe.

    Short-video captions often list ingredients without amounts or steps;
    those cases read the video itself before giving up.
    """
    required = [item for item in recipe.ingredients if not item.optional]
    return (
        is_cookable(recipe)
        and len(required) >= MIN_INGREDIENTS_FOR_CAPTION
        and recipe.quantified_share >= MIN_QUANTIFIED_SHARE_FOR_CAPTION
    )


def require_recipe(recipe: ExtractedRecipe) -> ExtractedRecipe:
    """Raise NOT_A_RECIPE unless there is at least an ingredient list."""
    if recipe.is_recipe and recipe.ingredients:
        return recipe
    raise RecipeImportError(
        RecipeImportFailure.NOT_A_RECIPE,
        "No recipe was found in this source.",
        details={"reason": recipe.rejection_reason}
        if recipe.rejection_reason
        else None,
    )


# Caption plus subtitles shorter than this, hashtags, mentions and links
# aside, cannot hold a recipe; reading the video directly saves an
# extraction call.
MIN_CAPTION_CHARS = 80
_NOT_RECIPE_TEXT = re.compile(
    r"(?:https?://\S+|[#@][\w.]+|[^\w\s,.:;()/%-])", re.UNICODE
)


def has_substantial_caption(material: RecipeSourceMaterial) -> bool:
    if material.structured_recipe is not None:
        return True
    caption = f"{material.description or ''} {material.subtitles or ''}"
    text = " ".join(_NOT_RECIPE_TEXT.sub(" ", caption).split())
    return len(text) >= MIN_CAPTION_CHARS

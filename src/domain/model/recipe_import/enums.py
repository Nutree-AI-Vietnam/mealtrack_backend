"""Enumerations for importing recipes from links, pasted text and photos."""

from __future__ import annotations

from enum import StrEnum


class RecipeImportSourceType(StrEnum):
    """What the user handed us."""

    URL = "url"
    TEXT = "text"
    IMAGE = "image"


class RecipeSourcePlatform(StrEnum):
    """Where the recipe came from; video platforms take the video path."""

    WEBSITE = "website"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TEXT = "text"
    IMAGE = "image"


VIDEO_PLATFORMS = frozenset(
    {
        RecipeSourcePlatform.YOUTUBE,
        RecipeSourcePlatform.TIKTOK,
        RecipeSourcePlatform.INSTAGRAM,
        RecipeSourcePlatform.FACEBOOK,
    }
)


class RecipeSignal(StrEnum):
    """Pieces of the source the recipe was actually read from."""

    STRUCTURED_DATA = "structured_data"
    # Structured data of a full-recipe page a video's caption links to.
    LINKED_RECIPE = "linked_recipe"
    PAGE_TEXT = "page_text"
    CAPTION = "caption"
    SUBTITLES = "subtitles"
    SPEECH = "speech"
    ON_SCREEN_TEXT = "on_screen_text"
    USER_TEXT = "user_text"
    PHOTO = "photo"


class RecipeImportWarning(StrEnum):
    """Things the review screen should point out before the user saves."""

    SERVINGS_ESTIMATED = "servings_estimated"
    QUANTITIES_ESTIMATED = "quantities_estimated"
    NUTRITION_PARTIAL = "nutrition_partial"
    NUTRITION_ESTIMATED = "nutrition_estimated"
    VIDEO_NOT_ANALYZED = "video_not_analyzed"


class IngredientNutritionSource(StrEnum):
    """Which nutrition tier priced an ingredient."""

    FOOD_REFERENCE = "food_reference"
    FATSECRET = "fatsecret"
    AI_ESTIMATE = "ai_estimate"

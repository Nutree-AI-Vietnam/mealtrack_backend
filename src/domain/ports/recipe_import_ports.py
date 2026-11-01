"""Ports for reading recipe sources and pricing their ingredients."""

from __future__ import annotations

from typing import Any, Protocol

from src.domain.model.recipe_import.enums import RecipeSourcePlatform
from src.domain.model.recipe_import.extracted_recipe import ExtractedRecipe
from src.domain.model.recipe_import.portions import PortionEstimate
from src.domain.model.recipe_import.source_material import (
    RecipeSourceMaterial,
    VideoMediaAnalysis,
)
from src.domain.services.meal_suggestion.nutrition_lookup_service import MealMacros


class RecipeWebPagePort(Protocol):
    """Fetch a public web page and collect its recipe data and text."""

    async def read_page(self, url: str) -> RecipeSourceMaterial:
        """Raise ``RecipeImportError`` for blocked, failing or unusable pages."""
        ...


class RecipeVideoSourcePort(Protocol):
    """Read a short video's caption cheaply, and its media when needed."""

    @property
    def media_analysis_enabled(self) -> bool:
        """Whether ``analyze_media`` can run in this process."""
        ...

    async def read_video(
        self, url: str, platform: RecipeSourcePlatform, *, language: str
    ) -> RecipeSourceMaterial:
        """Title, caption, subtitles and thumbnail, without downloading media."""
        ...

    async def analyze_media(
        self, material: RecipeSourceMaterial, *, language: str
    ) -> VideoMediaAnalysis:
        """Speech and on-screen text of the video itself."""
        ...


class RecipeExtractorPort(Protocol):
    """Structured recipe extraction by the AI provider."""

    async def extract(
        self, material: RecipeSourceMaterial, *, language: str
    ) -> ExtractedRecipe: ...

    async def read_photo(
        self, image_bytes: bytes, *, language: str, note: str | None = None
    ) -> ExtractedRecipe: ...

    async def estimate_portions(self, recipe: ExtractedRecipe) -> PortionEstimate:
        """Weight and eaten share of every line, and the servings."""
        ...


class IngredientNutritionPort(Protocol):
    """Backend nutrition for ``[{"name", "amount", "unit"}]`` lines."""

    async def calculate_meal_macros(
        self, ingredients: list[dict[str, Any]]
    ) -> MealMacros: ...


class RecipeImageArchivePort(Protocol):
    """Copy a third-party recipe image into the app's own image store."""

    async def archive(self, image_url: str) -> str | None:
        """Return the stored image URL, or None when it could not be copied."""
        ...

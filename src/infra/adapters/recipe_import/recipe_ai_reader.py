"""Structured recipe extraction through the shared AI model manager."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.model.ai.model_purpose import ModelPurpose
from src.domain.model.recipe_import.extracted_recipe import ExtractedRecipe
from src.domain.model.recipe_import.limits import MAX_ON_SCREEN_TEXT_CHARS
from src.domain.model.recipe_import.sanitizer import (
    sanitize_extracted_recipe,
)
from src.domain.model.recipe_import.source_material import RecipeSourceMaterial
from src.domain.model.recipe_import.text import clean_text
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.infra.adapters.recipe_import.recipe_prompts import (
    FRAMES_SYSTEM,
    OnScreenTextOutput,
    RecipeExtractionOutput,
    extraction_prompt,
    extraction_system,
    photo_prompt,
    photo_system,
)

logger = logging.getLogger(__name__)

# Long recipes with translated text run to a few thousand output tokens.
EXTRACTION_MAX_TOKENS = 8_000
FRAMES_MAX_TOKENS = 1_500


class RecipeAiReader:
    """``RecipeExtractorPort`` plus the on-screen text reader for videos."""

    def __init__(
        self,
        *,
        generation_service: MealGenerationServicePort,
        vision_manager: Any,
    ) -> None:
        self._generation = generation_service
        # AIModelManager: vision calls with purpose routing and fallback.
        self._vision = vision_manager

    async def extract(
        self, material: RecipeSourceMaterial, *, language: str
    ) -> ExtractedRecipe:
        raw = await self._generation.generate_meal_plan_async(
            prompt=extraction_prompt(material, language),
            system_message=extraction_system(language),
            response_type="json",
            max_tokens=EXTRACTION_MAX_TOKENS,
            schema=RecipeExtractionOutput,
            model_purpose="recipe_import",
        )
        return sanitize_extracted_recipe(_as_dict(raw), language=language)

    async def read_photo(
        self, image_bytes: bytes, *, language: str, note: str | None = None
    ) -> ExtractedRecipe:
        raw = await self._vision.generate_with_vision(
            purpose=ModelPurpose.RECIPE_IMAGE_SCAN,
            prompt=photo_prompt(language, note),
            image_data=image_bytes,
            system_message=photo_system(language),
            schema=RecipeExtractionOutput,
            max_tokens=EXTRACTION_MAX_TOKENS,
            image_mime_type="image/jpeg",
        )
        return sanitize_extracted_recipe(_as_dict(raw), language=language)

    async def read_frames(self, contact_sheet: bytes) -> str | None:
        raw = await self._vision.generate_with_vision(
            purpose=ModelPurpose.RECIPE_IMAGE_SCAN,
            prompt="Copy the recipe text shown in these video frames.",
            image_data=contact_sheet,
            system_message=FRAMES_SYSTEM,
            schema=OnScreenTextOutput,
            max_tokens=FRAMES_MAX_TOKENS,
            image_mime_type="image/jpeg",
        )
        lines = _as_dict(raw).get("lines")
        if not isinstance(lines, list):
            return None
        unique = list(
            dict.fromkeys(
                line.strip() for line in lines if isinstance(line, str) and line.strip()
            )
        )
        return clean_text(
            "\n".join(unique), max_chars=MAX_ON_SCREEN_TEXT_CHARS, multiline=True
        )


def _as_dict(raw: Any) -> dict[str, Any]:
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    if isinstance(raw, dict):
        return raw
    logger.warning("Recipe AI returned %s instead of an object", type(raw).__name__)
    return {}

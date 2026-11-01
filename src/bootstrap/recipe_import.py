"""Composition root for recipe import (fetcher, readers, AI, pricing)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from src.app.services.recipe_import import (
    RecipeImportGuard,
    RecipeImportService,
    RecipeNutritionPricer,
)
from src.domain.model.recipe_import.limits import (
    DAILY_IMPORTS_PER_USER,
    IMPORT_DEADLINE_SECONDS,
)
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.ports.recipe_import_ports import IngredientNutritionPort
from src.infra.adapters.meal_generation_service import MealGenerationService
from src.infra.adapters.recipe_import.audio_transcriber import OpenAIAudioTranscriber
from src.infra.adapters.recipe_import.recipe_ai_reader import RecipeAiReader
from src.infra.adapters.recipe_import.recipe_image_archive import RecipeImageArchive
from src.infra.adapters.recipe_import.safe_fetch import SafeHttpFetcher
from src.infra.adapters.recipe_import.video_media_analyzer import VideoMediaAnalyzer
from src.infra.adapters.recipe_import.video_source_reader import (
    VideoRecipeSourceReader,
)
from src.infra.adapters.recipe_import.web_page_reader import WebRecipePageReader
from src.infra.config.settings import get_settings
from src.infra.http.image_download import ScanImageDownloadAdapter
from src.infra.services.ai.ai_model_manager import AIModelManager

CounterIncrement = Callable[[str, int], Awaitable[int | None]]


@lru_cache
def get_recipe_fetcher() -> SafeHttpFetcher:
    return SafeHttpFetcher()


@lru_cache
def get_recipe_ai_reader() -> RecipeAiReader:
    return RecipeAiReader(
        generation_service=MealGenerationService(),
        vision_manager=AIModelManager.get_instance(),
    )


@lru_cache
def get_recipe_video_reader() -> VideoRecipeSourceReader:
    settings = get_settings()
    fetcher = get_recipe_fetcher()
    transcriber = (
        OpenAIAudioTranscriber(
            api_key=settings.OPENAI_API_KEY,
            model=settings.RECIPE_IMPORT_TRANSCRIPTION_MODEL,
        )
        if settings.OPENAI_API_KEY
        else None
    )
    return VideoRecipeSourceReader(
        fetcher=fetcher,
        page_reader=WebRecipePageReader(fetcher),
        media_analyzer=VideoMediaAnalyzer(
            reader=get_recipe_ai_reader(), transcriber=transcriber
        ),
    )


def build_recipe_import_service(
    nutrition: IngredientNutritionPort,
) -> RecipeImportService:
    fetcher = get_recipe_fetcher()
    return RecipeImportService(
        web=WebRecipePageReader(fetcher),
        video=get_recipe_video_reader(),
        extractor=get_recipe_ai_reader(),
        pricer=RecipeNutritionPricer(nutrition),
        download_image=ScanImageDownloadAdapter(),
    )


def build_recipe_image_archive(
    image_store: ImageStorePort, own_hosts: frozenset[str]
) -> RecipeImageArchive:
    return RecipeImageArchive(
        fetcher=get_recipe_fetcher(), image_store=image_store, own_hosts=own_hosts
    )


def build_recipe_import_guard(increment_counter: CounterIncrement) -> RecipeImportGuard:
    return RecipeImportGuard(increment_counter=increment_counter)


def recipe_import_capabilities() -> dict[str, Any]:
    return {
        "sources": ["url", "text", "image"],
        "video_platforms": ["youtube", "tiktok", "instagram", "facebook"],
        # False when ffmpeg is missing: videos are then read from captions only.
        "video_media_analysis": get_recipe_video_reader().media_analysis_enabled,
        "daily_limit": DAILY_IMPORTS_PER_USER,
        "timeout_seconds": IMPORT_DEADLINE_SECONDS,
    }

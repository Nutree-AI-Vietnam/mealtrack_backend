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
    return SafeHttpFetcher(
        timeout_seconds=get_settings().RECIPE_IMPORT_FETCH_TIMEOUT_SECONDS
    )


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
    media: VideoMediaAnalyzer | None = None
    if (
        settings.RECIPE_IMPORT_VIDEO_ENABLED
        and settings.RECIPE_IMPORT_VIDEO_MEDIA_ENABLED
    ):
        transcriber = (
            OpenAIAudioTranscriber(
                api_key=settings.OPENAI_API_KEY,
                model=settings.RECIPE_IMPORT_TRANSCRIPTION_MODEL,
            )
            if settings.OPENAI_API_KEY
            else None
        )
        media = VideoMediaAnalyzer(
            reader=get_recipe_ai_reader(),
            transcriber=transcriber,
            cookies_file=settings.RECIPE_IMPORT_YTDLP_COOKIES_FILE,
            max_seconds=settings.RECIPE_IMPORT_VIDEO_MAX_SECONDS,
            max_bytes=settings.RECIPE_IMPORT_VIDEO_MAX_BYTES,
            frame_count=settings.RECIPE_IMPORT_VIDEO_FRAMES,
            concurrency=settings.RECIPE_IMPORT_MEDIA_CONCURRENCY,
        )
    return VideoRecipeSourceReader(
        fetcher=fetcher,
        page_reader=WebRecipePageReader(fetcher),
        media_analyzer=media,
        cookies_file=settings.RECIPE_IMPORT_YTDLP_COOKIES_FILE,
        ytdlp_enabled=settings.RECIPE_IMPORT_VIDEO_ENABLED,
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
    settings = get_settings()
    return RecipeImportGuard(
        enabled=settings.RECIPE_IMPORT_ENABLED,
        daily_limit=settings.RECIPE_IMPORT_DAILY_LIMIT,
        increment_counter=increment_counter,
    )


def recipe_import_timeout_seconds() -> float:
    return get_settings().RECIPE_IMPORT_TIMEOUT_SECONDS


def recipe_import_capabilities() -> dict[str, Any]:
    settings = get_settings()
    video = get_recipe_video_reader()
    return {
        "enabled": settings.RECIPE_IMPORT_ENABLED,
        "sources": ["url", "text", "image"],
        "video_platforms": ["youtube", "tiktok", "instagram", "facebook"],
        "video_captions": settings.RECIPE_IMPORT_VIDEO_ENABLED,
        "video_media_analysis": video.media_analysis_enabled,
        "daily_limit": settings.RECIPE_IMPORT_DAILY_LIMIT,
        "timeout_seconds": settings.RECIPE_IMPORT_TIMEOUT_SECONDS,
    }

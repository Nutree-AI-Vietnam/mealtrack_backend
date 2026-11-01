"""FastAPI wiring for recipe import."""

from __future__ import annotations

from functools import lru_cache

from src.api.base_dependencies import (
    get_allowed_image_hosts,
    get_image_store,
    get_nutrition_lookup_service,
    get_recipe_import_guard,
)
from src.app.services.recipe_import import RecipeImportService, RecipeNutritionPricer
from src.bootstrap.recipe_import import (
    build_recipe_image_archive,
    build_recipe_import_service,
    recipe_import_capabilities,
    recipe_import_timeout_seconds,
)
from src.domain.ports.recipe_import_ports import RecipeImageArchivePort

__all__ = [
    "get_recipe_image_archive",
    "get_recipe_import_guard",
    "get_recipe_import_pricer",
    "get_recipe_import_service",
    "recipe_import_capabilities",
    "recipe_import_timeout_seconds",
]


@lru_cache
def get_recipe_import_service() -> RecipeImportService:
    return build_recipe_import_service(get_nutrition_lookup_service())


@lru_cache
def get_recipe_import_pricer() -> RecipeNutritionPricer:
    return RecipeNutritionPricer(get_nutrition_lookup_service())


@lru_cache
def get_recipe_image_archive() -> RecipeImageArchivePort:
    return build_recipe_image_archive(get_image_store(), get_allowed_image_hosts())

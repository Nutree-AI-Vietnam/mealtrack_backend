"""Domain port for canonical food-reference lookups."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class FoodReferenceServingProjection:
    """Food-specific serving conversion data."""

    name: str
    grams: float | None
    milliliters: float | None
    is_default: bool = False


@dataclass(frozen=True)
class FoodReferenceNutritionProjection:
    """Canonical nutrition snapshot used by catalog publication."""

    id: int
    name: str
    source: str
    is_verified: bool
    protein_100g: float | None
    carbs_100g: float | None
    fat_100g: float | None
    fiber_100g: float = 0.0
    sugar_100g: float = 0.0
    density_g_ml: float | None = None
    servings: list[FoodReferenceServingProjection] = field(default_factory=list)
    name_normalized: str | None = None
    source_namespace: str | None = None
    source_food_id: str | None = None


@dataclass(frozen=True)
class FoodReferenceSearchProjection:
    """Local food search result shaped before API response mapping."""

    id: int
    name: str
    name_normalized: str | None
    brand: str | None
    source: str
    is_verified: bool
    protein_100g: float | None
    carbs_100g: float | None
    fat_100g: float | None
    fiber_100g: float = 0.0
    sugar_100g: float = 0.0
    serving_size: str | None = None
    serving_options: list[dict] = field(default_factory=list)
    source_namespace: str | None = None
    source_food_id: str | None = None
    name_vi: str | None = None


class FoodReferenceRepositoryPort(Protocol):
    """Repository contract for typed canonical food-reference projections."""

    async def get_nutrition_projection(
        self,
        food_reference_id: int,
    ) -> FoodReferenceNutritionProjection | None:
        """Return one canonical food-reference nutrition projection."""

    async def get_nutrition_projections(
        self,
        food_reference_ids: list[int],
        *,
        for_update: bool = False,
    ) -> dict[int, FoodReferenceNutritionProjection]:
        """Return canonical projections for a deduplicated ID batch."""

    async def list_catalog_seed_candidates(
        self,
    ) -> list[FoodReferenceNutritionProjection]:
        """Return lightweight projections for catalog ingredient resolver ranking."""

    async def find_catalog_seed_candidates_by_normalized_name(
        self,
        name_normalized: str,
    ) -> list[FoodReferenceNutritionProjection]:
        """Return candidate projections by exact normalized name for seed imports."""

    async def approve_for_catalog_seed(
        self,
        food_reference_id: int,
    ) -> FoodReferenceNutritionProjection | None:
        """Mark one admin-reviewed food reference as eligible for catalog publication."""

    async def search_local(
        self,
        query: str,
        region: str,
        limit: int,
    ) -> list[FoodReferenceSearchProjection]:
        """Return bounded, verified-first local search results."""

    async def find_by_source_identity(
        self,
        namespace: str,
        food_id: str,
    ) -> dict[str, Any] | None:
        """Return the food-reference row already tagged with this provider id."""

    async def adopt_provider_food(
        self,
        namespace: str,
        food_id: str,
        english_name: str,
        per_100g: dict[str, Any],
        servings: list[dict[str, Any]] | None,
        locale: str,
        locale_name: str,
    ) -> dict[str, Any]:
        """Adopt one identity-scoped provider food and persist ``name_vi``."""

    async def find_by_locale_names(
        self,
        language: str,
        names: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Return eligible rows whose display name matches (casefold) exactly."""

    async def get_display_projections(
        self,
        food_reference_ids: list[int],
        language: str | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Return id-keyed display names for already-linked meal lines."""

    async def get_serving_phrase_translations(
        self,
        phrases: list[str],
        language: str,
    ) -> dict[str, str]:
        """Return cached exact serving-phrase translations."""

    async def upsert_serving_phrase_translations(
        self,
        labels_by_source: dict[str, str],
        language: str,
    ) -> None:
        """Persist exact serving-phrase translations for reuse."""

    async def apply_serving_name_vi(
        self,
        food_reference_id: int,
        labels_by_unit: dict[str, str],
    ) -> None:
        """Write Vietnamese serving labels onto matching catalog rows."""

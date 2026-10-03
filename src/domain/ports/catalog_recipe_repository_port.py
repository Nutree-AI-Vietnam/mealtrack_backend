"""Repository port for curated catalog meal projections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.domain.model.meal_recommendation.catalog_recipe import (
    CatalogMeal,
    normalize_catalog_ingredient_category,
)
from src.domain.model.meal_recommendation.catalog_selection_features import (
    CatalogPublicationVersion,
)

MAX_CATALOG_POPULARITY_RANK = 2_147_483_647


@dataclass(frozen=True)
class CatalogMealSeedIngredientWrite:
    """Ingredient payload for additive catalog seed imports."""

    display_name: str
    quantity: float
    unit: str
    category: str = "pantry"
    food_reference_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "category",
            normalize_catalog_ingredient_category(self.category),
        )


@dataclass(frozen=True)
class CatalogMealSeedWrite:
    """Display-only meal payload for additive catalog seed imports."""

    catalog_key: str
    content_hash: str
    name: str
    cuisine: str
    description: str | None
    image_url: str | None
    meal_types: tuple[str, ...]
    ingredients: tuple[CatalogMealSeedIngredientWrite, ...]
    popularity_rank: int | None = None
    source_name: str | None = None
    source_url: str | None = None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    tag: str | None = None
    allergens: str | None = None
    summary: str | None = None
    equipment: str | None = None
    base_servings: int | None = None
    serving_source: str | None = None
    serving_confidence: str = "unknown"
    steps: tuple[tuple[int, str, str], ...] = ()
    nutrition: dict[str, Any] | None = None


@dataclass(frozen=True)
class CatalogMealSeedExisting:
    """Minimal existing-row projection used for duplicate protection."""

    catalog_key: str
    content_hash: str


@dataclass(frozen=True)
class CatalogMealSeedSignature:
    """Canonical signature used to withhold near-duplicate seed meals."""

    catalog_key: str
    content_hash: str
    normalized_name: str
    normalized_cuisine: str
    food_reference_ids: frozenset[int]


@dataclass(frozen=True, order=True)
class CatalogMealRevision:
    """Comparable active catalog revision."""

    active_count: int
    catalog_updated_at: datetime | None
    food_reference_updated_at: datetime | None


@dataclass(frozen=True)
class CatalogPopularPage:
    """One ranked popular-feed page plus ranking-gate counts."""

    items: tuple[CatalogMeal, ...]
    total: int
    any_ranked: bool
    unranked_count: int


@dataclass(frozen=True)
class CatalogRecipePage:
    items: tuple[CatalogMeal, ...]
    total: int
    projected: bool = False


class CatalogMealRepositoryPort(ABC):
    """Read/write contract for catalog meals during the rework."""

    async def get_meal_summaries(
        self, catalog_meal_ids: Iterable[str]
    ) -> list[CatalogMeal]:
        """Bounded compatibility implementation for older repository adapters."""
        return await self.get_meals(catalog_meal_ids)

    async def list_selection_candidates(self) -> list[CatalogMeal]:
        return await self.list_active_meals()

    async def list_recipe_page(
        self,
        *,
        query: str | None = None,
        diet: str | None = None,
        max_cook_time: int | None = None,
        cuisine: str | None = None,
        meal_type: str | None = None,
        dislikes: tuple[str, ...] = (),
        allergies: tuple[str, ...] = (),
        limit: int = 20,
        offset: int = 0,
    ) -> CatalogRecipePage:
        """Apply every eligibility filter before counting and paging."""
        raise NotImplementedError

    async def capture_catalog_publication_version(self) -> CatalogPublicationVersion:
        raise NotImplementedError

    async def lock_catalog_publication(
        self, *, shared: bool = True
    ) -> CatalogPublicationVersion:
        """Acquire the fence before owner/week and plan locks in final writes."""
        raise NotImplementedError

    @abstractmethod
    async def list_allergen_codes(self) -> list[str]:
        """Return canonical codes from the global allergen reference."""

    @abstractmethod
    async def list_active_meals(
        self,
        *,
        cuisine: str | None = None,
        meal_type: str | None = None,
    ) -> list[CatalogMeal]:
        """Return active catalog meals."""

    @abstractmethod
    async def list_popular_page(
        self,
        *,
        limit: int,
        offset: int,
        query: str | None = None,
        cuisine: str | None = None,
        meal_type: str | None = None,
        shuffle_seed: str | None = None,
    ) -> CatalogPopularPage:
        """Return one popularity-ranked page without loading the full catalog."""

    @abstractmethod
    async def get_active_catalog_revision(self) -> CatalogMealRevision:
        """Return a lightweight comparable active catalog revision."""

    @abstractmethod
    async def get_meal(self, catalog_meal_id: str) -> CatalogMeal | None:
        """Return one active catalog meal."""

    @abstractmethod
    async def get_meals(self, catalog_meal_ids: Iterable[str]) -> list[CatalogMeal]:
        """Return active catalog meals for a bounded set of IDs."""

    @abstractmethod
    async def get_meal_detail(self, catalog_meal_id: str) -> CatalogMeal | None:
        """Return one active catalog meal with ordered detail steps."""

    @abstractmethod
    async def get_micronutrient_enrichment(
        self, *, catalog_meal_id: str, content_hash: str
    ) -> dict | None:
        """Return a cached micronutrient estimate for one recipe revision."""

    @abstractmethod
    async def get_micronutrient_enrichment_status(
        self, *, catalog_meal_id: str, content_hash: str
    ) -> str | None:
        """Return readiness, backoff, or lease state for an enrichment claim."""

    @abstractmethod
    async def claim_micronutrient_enrichment(
        self,
        *,
        catalog_meal_id: str,
        content_hash: str,
        lease_seconds: int,
    ) -> tuple[str, str | None]:
        """Claim cold enrichment work and return a fencing token when claimed."""

    @abstractmethod
    async def save_micronutrient_enrichment(
        self,
        *,
        catalog_meal_id: str,
        content_hash: str,
        claim_token: str,
        micros: dict[str, float],
        sources: dict[str, str],
    ) -> bool:
        """Save estimate only while the revision and claim token still match."""

    @abstractmethod
    async def fail_micronutrient_enrichment(
        self,
        *,
        catalog_meal_id: str,
        content_hash: str,
        claim_token: str,
        retry_seconds: int,
    ) -> None:
        """Release the claim and apply a bounded retry backoff."""

    @abstractmethod
    async def find_seed_existing(
        self,
        *,
        catalog_key: str,
        content_hash: str,
    ) -> CatalogMealSeedExisting | None:
        """Return an existing catalog seed row by key or content hash."""

    @abstractmethod
    async def add_seed_meal(self, seed: CatalogMealSeedWrite) -> None:
        """Add one display-only catalog seed meal without owning commit."""

    @abstractmethod
    async def update_popularity_rank(
        self, *, catalog_key: str, popularity_rank: int | None
    ) -> None:
        """Update the editorial rank for an existing catalog seed."""

    @abstractmethod
    async def lock_seed_import(self) -> None:
        """Acquire a transaction-scoped lock for seed import writes."""

    @abstractmethod
    async def list_seed_signatures(self) -> list[CatalogMealSeedSignature]:
        """Return canonical signatures for duplicate review."""

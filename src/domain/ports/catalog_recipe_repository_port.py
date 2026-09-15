"""Repository port for curated catalog meal projections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from src.domain.model.meal_recommendation.catalog_recipe import CatalogMeal

MAX_CATALOG_POPULARITY_RANK = 2_147_483_647


@dataclass(frozen=True)
class CatalogMealSeedIngredientWrite:
    """Ingredient payload for additive catalog seed imports."""

    food_reference_id: int
    display_name: str
    quantity: float
    unit: str


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
    imaged_count: int = 0


@dataclass(frozen=True)
class CatalogPopularPage:
    """One ranked popular-feed page plus ranking-gate counts."""

    items: tuple[CatalogMeal, ...]
    total: int
    any_ranked: bool
    unranked_count: int


class CatalogMealRepositoryPort(ABC):
    """Read/write contract for catalog meals during the rework."""

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

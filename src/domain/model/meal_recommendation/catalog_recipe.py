"""Domain projections for curated catalog meals."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from src.domain.model.meal_recommendation.catalog_selection_features import (
    CatalogSelectionFeatures,
)
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros
from src.domain.services.meal_recommendation.ingredient_quantity_normalization import (
    normalize_ingredient_quantity,
)

ALLOWED_CATALOG_INGREDIENT_CATEGORIES: frozenset[str] = frozenset(
    {"produce", "protein", "pantry"}
)

PRODUCE_CATEGORY_ALIASES: frozenset[str] = frozenset(
    {"produce", "fresh_produce", "vegetable", "vegetables", "fruit", "fruits"}
)
PROTEIN_CATEGORY_ALIASES: frozenset[str] = frozenset(
    {"protein", "meat", "seafood", "poultry", "fish"}
)


def normalize_catalog_ingredient_category(raw_category: object) -> str:
    """Normalize raw ingredient categories to the allowed catalog check constraint values.

    The database constraint ``ck_meal_catalog_ingredients_category`` restricts
    category to ('produce', 'protein', 'pantry').
    """
    category = str(raw_category or "pantry").strip().casefold()
    if category in PRODUCE_CATEGORY_ALIASES:
        return "produce"
    if category in PROTEIN_CATEGORY_ALIASES:
        return "protein"
    return "pantry"


@dataclass(frozen=True)
class CatalogMealIngredient:
    """Ingredient reference for a catalog meal."""

    food_reference_id: int | None
    display_name: str
    quantity: Decimal
    unit: str
    category: str = "pantry"
    position: int | None = None
    canonical_amount: Decimal | None = None
    canonical_unit: str | None = None
    quantity_dimension: str | None = None
    quantity_confidence: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "category",
            normalize_catalog_ingredient_category(self.category),
        )
        normalized = normalize_ingredient_quantity(self.quantity, self.unit)
        if self.canonical_amount is None:
            object.__setattr__(self, "canonical_amount", normalized.amount)
        if self.canonical_unit is None:
            object.__setattr__(self, "canonical_unit", normalized.unit)
        if self.quantity_dimension is None:
            object.__setattr__(self, "quantity_dimension", normalized.dimension)
        if self.quantity_confidence == "unknown":
            object.__setattr__(self, "quantity_confidence", normalized.confidence)

    @property
    def name(self) -> str:
        """Compatibility for scoring/materialization call sites during rework."""

        return self.display_name


@dataclass(frozen=True)
class CatalogMealStep:
    """One ordered, curated cooking instruction."""

    step_number: int
    title: str
    description: str


@dataclass(frozen=True)
class CatalogMeal:
    """Renderable catalog meal consumed by deterministic recommendation planning."""

    id: str
    catalog_key: str
    content_hash: str
    name: str
    cuisine: str
    description: str | None
    image_url: str | None
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal
    fiber_g: Decimal
    sugar_g: Decimal = Decimal("0")
    meal_types: tuple[str, ...] = field(default_factory=tuple)
    ingredients: tuple[CatalogMealIngredient, ...] = field(default_factory=tuple)
    is_active: bool = True
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
    steps: tuple[CatalogMealStep, ...] = field(default_factory=tuple)
    publication_status: str = "published"
    nutrition_status: str = "ready"
    allergen_codes: tuple[str, ...] = ()
    recipe_payload: dict | None = None
    ai_nutrition_estimate: dict | None = None
    nutrition_micros: Micros | None = None
    nutrition_micros_sources: dict[str, str] = field(default_factory=dict)
    nutrition_micros_estimated: bool = False
    nutrition_micros_enrichment_loaded: bool = False
    selection_features: CatalogSelectionFeatures | None = None

    @property
    def calories(self) -> int:
        """Backend-derived calories from macro totals."""

        protein = float(self.protein_g)
        carbs = float(self.carbs_g)
        fat = float(self.fat_g)
        fiber = float(self.fiber_g)
        return round(Macros.raw_total_calories(protein, carbs, fat, fiber))

    @property
    def status(self) -> str:
        """Compatibility for optimizer filtering during the rework."""

        return "published" if self.is_active else "retired"

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "id": self.id,
            "catalog_key": self.catalog_key,
            "content_hash": self.content_hash,
            "name": self.name,
            "cuisine": self.cuisine,
            "description": self.description,
            "image_url": self.image_url,
            "protein_g": str(self.protein_g),
            "carbs_g": str(self.carbs_g),
            "fat_g": str(self.fat_g),
            "fiber_g": str(self.fiber_g),
            "sugar_g": str(self.sugar_g),
            "meal_types": list(self.meal_types),
            "ingredients": [
                {
                    "food_reference_id": ing.food_reference_id,
                    "display_name": ing.display_name,
                    "quantity": str(ing.quantity),
                    "unit": ing.unit,
                    "category": ing.category,
                    "position": ing.position,
                    "canonical_amount": (
                        str(ing.canonical_amount)
                        if ing.canonical_amount is not None
                        else None
                    ),
                    "canonical_unit": ing.canonical_unit,
                    "quantity_dimension": ing.quantity_dimension,
                    "quantity_confidence": ing.quantity_confidence,
                }
                for ing in self.ingredients
            ],
            "is_active": self.is_active,
            "popularity_rank": self.popularity_rank,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "prep_time_minutes": self.prep_time_minutes,
            "cook_time_minutes": self.cook_time_minutes,
            "tag": self.tag,
            "allergens": self.allergens,
            "summary": self.summary,
            "equipment": self.equipment,
            "base_servings": self.base_servings,
            "serving_source": self.serving_source,
            "serving_confidence": self.serving_confidence,
            "steps": [
                {
                    "step_number": s.step_number,
                    "title": s.title,
                    "description": s.description,
                }
                for s in self.steps
            ],
            "publication_status": self.publication_status,
            "nutrition_status": self.nutrition_status,
            "allergen_codes": list(self.allergen_codes),
            "recipe_payload": self.recipe_payload,
            "ai_nutrition_estimate": self.ai_nutrition_estimate,
            "nutrition_micros": (
                self.nutrition_micros.to_dict() if self.nutrition_micros else None
            ),
            "nutrition_micros_sources": self.nutrition_micros_sources,
            "nutrition_micros_estimated": self.nutrition_micros_estimated,
            "nutrition_micros_enrichment_loaded": self.nutrition_micros_enrichment_loaded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CatalogMeal:
        """Reconstruct CatalogMeal from dictionary."""
        return cls(
            id=data["id"],
            catalog_key=data["catalog_key"],
            content_hash=data["content_hash"],
            name=data["name"],
            cuisine=data["cuisine"],
            description=data.get("description"),
            image_url=data.get("image_url"),
            protein_g=Decimal(str(data.get("protein_g", 0))),
            carbs_g=Decimal(str(data.get("carbs_g", 0))),
            fat_g=Decimal(str(data.get("fat_g", 0))),
            fiber_g=Decimal(str(data.get("fiber_g", 0))),
            sugar_g=Decimal(str(data.get("sugar_g", 0))),
            meal_types=tuple(data.get("meal_types", ())),
            ingredients=tuple(
                CatalogMealIngredient(
                    food_reference_id=ing.get("food_reference_id"),
                    display_name=ing["display_name"],
                    quantity=Decimal(str(ing["quantity"])),
                    unit=ing["unit"],
                    category=ing.get("category", "pantry"),
                    position=ing.get("position"),
                    canonical_amount=(
                        Decimal(str(ing["canonical_amount"]))
                        if ing.get("canonical_amount") is not None
                        else None
                    ),
                    canonical_unit=ing.get("canonical_unit"),
                    quantity_dimension=ing.get("quantity_dimension"),
                    quantity_confidence=ing.get("quantity_confidence", "unknown"),
                )
                for ing in data.get("ingredients", ())
            ),
            is_active=data.get("is_active", True),
            popularity_rank=data.get("popularity_rank"),
            source_name=data.get("source_name"),
            source_url=data.get("source_url"),
            prep_time_minutes=data.get("prep_time_minutes"),
            cook_time_minutes=data.get("cook_time_minutes"),
            tag=data.get("tag"),
            allergens=data.get("allergens"),
            summary=data.get("summary"),
            equipment=data.get("equipment"),
            base_servings=data.get("base_servings"),
            serving_source=data.get("serving_source"),
            serving_confidence=data.get("serving_confidence", "unknown"),
            steps=tuple(
                CatalogMealStep(
                    step_number=s["step_number"],
                    title=s["title"],
                    description=s["description"],
                )
                for s in data.get("steps", ())
            ),
            publication_status=data.get("publication_status", "published"),
            nutrition_status=data.get("nutrition_status", "ready"),
            allergen_codes=tuple(data.get("allergen_codes", ())),
            recipe_payload=data.get("recipe_payload"),
            ai_nutrition_estimate=data.get("ai_nutrition_estimate"),
            nutrition_micros=(
                Micros.from_dict(data["nutrition_micros"])
                if data.get("nutrition_micros")
                else None
            ),
            nutrition_micros_sources=dict(data.get("nutrition_micros_sources", {})),
            nutrition_micros_estimated=data.get("nutrition_micros_estimated", False),
            nutrition_micros_enrichment_loaded=data.get(
                "nutrition_micros_enrichment_loaded", False
            ),
        )


class MealRecommendationInsufficiencyReason(StrEnum):
    """Typed reasons a deterministic recommendation plan cannot be produced."""

    NOT_ENOUGH_CURRENT_RECIPES = "not_enough_current_recipes"
    NOT_ENOUGH_ALTERNATIVES = "not_enough_alternatives"


@dataclass(frozen=True)
class MealRecommendationSlot:
    """One selected catalog meal slot in a deterministic recommendation plan."""

    day_index: int
    meal_type: str
    target_calories: int
    catalog_meal: CatalogMeal
    score: float

    @property
    def recipe(self) -> CatalogMeal:
        """Temporary optimizer compatibility for older call sites."""

        return self.catalog_meal


@dataclass(frozen=True)
class MealRecommendationAlternative:
    """Alternative catalog meal for a selected recommendation slot."""

    day_index: int
    meal_type: str
    target_calories: int
    catalog_meal: CatalogMeal
    score: float

    @property
    def recipe(self) -> CatalogMeal:
        """Temporary optimizer compatibility for older call sites."""

        return self.catalog_meal


@dataclass(frozen=True)
class MealRecommendationPlan:
    """Pure-domain deterministic recommendation result."""

    slots: tuple[MealRecommendationSlot, ...]
    alternatives: dict[tuple[int, str], tuple[MealRecommendationAlternative, ...]]


@dataclass(frozen=True)
class MealRecommendationInsufficiency:
    """Typed deterministic failure when catalog capacity is insufficient."""

    reason: MealRecommendationInsufficiencyReason
    message: str
    required: int
    available: int

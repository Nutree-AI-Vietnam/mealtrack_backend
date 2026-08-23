"""
App layer DTOs for meal-related operations.
Domain-agnostic - used by handlers, mapped to API DTOs at the presentation layer.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedFoodItemDto:
    """DTO for a parsed food item (app layer)."""

    name: str
    quantity: float
    unit: str
    protein: float
    carbs: float
    fat: float
    fiber: float = 0.0
    sugar: float = 0.0
    data_source: str | None = None
    fdc_id: int | None = None
    allowed_units: list[dict[str, Any]] | None = None
    food_id: str | None = None
    food_reference_id: int | None = None
    origin: str | None = None
    source_namespace: str | None = None
    source_food_id: str | None = None
    nutrition_basis: str | None = None
    nutrition_contract_version: str | None = None
    calories_per_100g: float | None = None
    protein_per_100g: float | None = None
    carbs_per_100g: float | None = None
    fat_per_100g: float | None = None
    fiber_per_100g: float | None = None
    sugar_per_100g: float | None = None
    canonical_name: str | None = None
    source_snapshot: dict[str, Any] | None = None


@dataclass
class ParseMealTextResponseDto:
    """DTO for parse meal text command response (app layer)."""

    items: list[ParsedFoodItemDto]
    total_protein: float
    total_carbs: float
    total_fat: float
    emoji: str | None = None
    unmatched_terms: list[str] = field(default_factory=list)

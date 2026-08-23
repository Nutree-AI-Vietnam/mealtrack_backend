"""
Domain model for food item changes.
Used when editing meals to add, update, or remove food items.
"""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class FoodItemChange:
    """Represents a change to a food item in meal editing."""

    action: str  # "add", "update", "remove"
    id: Optional[str] = None
    fdc_id: Optional[int] = None
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    custom_nutrition: Optional["CustomNutritionData"] = None
    nutrition_override: Optional["NutritionOverride"] = None
    clear_nutrition_override: bool = False
    override_intent: Optional[str] = None
    serving_options: Optional[list[dict[str, Any]]] = None
    origin: Optional[str] = None
    food_reference_id: Optional[int] = None
    source_namespace: Optional[str] = None
    source_food_id: Optional[str] = None
    source_snapshot: Optional[dict[str, Any]] = None


@dataclass
class CustomNutritionData:
    """Custom nutrition data for non-USDA ingredients."""

    calories_per_100g: float
    protein_per_100g: float
    carbs_per_100g: float
    fat_per_100g: float
    fiber_per_100g: float = 0.0
    sugar_per_100g: float = 0.0


@dataclass
class NutritionOverride:
    calories: float
    protein: float
    carbs: float
    fat: float

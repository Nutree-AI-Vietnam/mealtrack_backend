"""
Command to edit meal ingredients and portions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from src.app.events.base import Command
from src.domain.model.meal.food_item_change import (
    CustomNutritionData,
    FoodItemChange,
    NutritionOverride,
)


@dataclass
class EditMealCommand(Command):
    """Command to edit a meal's ingredients."""

    meal_id: str
    user_id: str = ""
    dish_name: Optional[str] = None
    created_at: Optional[datetime] = None
    meal_type: Optional[str] = None
    food_item_changes: List[FoodItemChange] = field(default_factory=list)
    nutrition_override: Optional[NutritionOverride] = None
    nutrition_contract_version: Optional[int] = None
    override_intent: Optional[str] = None
    idempotency_key: Optional[str] = None
    request_fingerprint: Optional[str] = None
    language: str = "en"


@dataclass
class AddCustomIngredientCommand(Command):
    """Command to add a custom ingredient to a meal."""

    meal_id: str
    name: str
    quantity: float
    unit: str
    nutrition: CustomNutritionData

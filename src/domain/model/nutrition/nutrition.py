from dataclasses import dataclass
from typing import Any
from .macros import Macros
from .micros import Micros

MAX_FOOD_ITEM_QUANTITY = 10000.0


@dataclass
class NutritionOverride:
    """Absolute nutrition values entered directly by a user."""

    calories: float
    protein: float
    carbs: float
    fat: float

    def to_dict(self) -> dict:
        return {
            "calories": self.calories,
            "protein": self.protein,
            "carbs": self.carbs,
            "fat": self.fat,
        }


@dataclass
class FoodItem:
    """Represents a single food item in a meal with nutritional information."""

    id: str
    name: str
    quantity: float
    unit: str
    macros: Macros
    micros: Micros | None = None
    confidence: float = 1.0  # 0.0-1.0 confidence score from AI or lookup
    fdc_id: int | None = None  # USDA FDC ID if available
    food_reference_id: int | None = None
    is_custom: bool = False  # Whether this is a custom ingredient
    serving_options: list[dict[str, Any]] | None = None
    nutrition_override: NutritionOverride | None = None
    source_kind: str | None = None
    source_food_id: str | None = None
    nutrition_contract_version: str | None = None
    source_snapshot: dict[str, Any] | None = None

    def __post_init__(self):
        """Validate invariants."""
        if not self.name or not self.name.strip():
            raise ValueError("Food item name cannot be empty")
        if len(self.name) > 200:
            raise ValueError(
                f"Food item name too long (max 200 chars): {len(self.name)}"
            )
        if self.quantity <= 0 or self.quantity > MAX_FOOD_ITEM_QUANTITY:
            raise ValueError(f"Quantity must be between 0 and 10000: {self.quantity}")
        if not 0 <= self.confidence <= 1:
            raise ValueError(f"Confidence must be between 0 and 1: {self.confidence}")
        if not self.unit or not self.unit.strip():
            raise ValueError("Unit cannot be empty")
        if len(self.unit) > 120:
            raise ValueError(f"Unit too long (max 120 chars): {len(self.unit)}")

    @property
    def calories(self) -> float:
        """Use a user-entered value when one exists."""
        return (
            self.nutrition_override.calories
            if self.nutrition_override
            else self.macros.total_calories
        )

    @property
    def effective_macros(self) -> Macros:
        if self.nutrition_override is None:
            return self.macros
        return Macros(
            protein=self.nutrition_override.protein,
            carbs=self.nutrition_override.carbs,
            fat=self.nutrition_override.fat,
            fiber=self.macros.fiber,
            sugar=self.macros.sugar,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        result = {
            "id": self.id,
            "name": self.name,
            "quantity": self.quantity,
            "unit": self.unit,
            "calories": self.calories,
            "macros": self.effective_macros.to_dict(),
            "confidence": self.confidence,
            "is_custom": self.is_custom,
        }
        if self.micros:
            result["micros"] = self.micros.to_dict()
        if self.fdc_id:
            result["fdc_id"] = self.fdc_id
        if self.food_reference_id:
            result["food_reference_id"] = self.food_reference_id
        if self.nutrition_override:
            result["nutrition_override"] = self.nutrition_override.to_dict()
        if self.source_kind:
            result["origin"] = self.source_kind
        if self.source_food_id:
            result["source_food_id"] = self.source_food_id
        if self.nutrition_contract_version:
            result["nutrition_contract_version"] = self.nutrition_contract_version
        if self.source_snapshot:
            result["source_snapshot"] = self.source_snapshot
        if self.serving_options:
            result["serving_options"] = self.serving_options
        return result


@dataclass
class Nutrition:
    """Value object representing full nutritional information for a meal."""

    macros: Macros
    micros: Micros | None = None
    food_items: list[FoodItem] | None = None
    confidence_score: float = 1.0  # 0.0-1.0 overall confidence score
    nutrition_override: NutritionOverride | None = None

    def __post_init__(self):
        """Validate invariants."""
        if not 0 <= self.confidence_score <= 1:
            raise ValueError(
                f"Confidence score must be between 0 and 1: {self.confidence_score}"
            )

        # Validate food items
        if self.food_items:
            if len(self.food_items) > 50:
                raise ValueError(
                    f"Too many ingredients (max 50): {len(self.food_items)}"
                )

    @property
    def calories(self) -> float:
        """Meal calories prefer meal override; else sum of item effective
        calories when any ingredient has a calorie override; else macros.
        """
        if self.nutrition_override is not None:
            return float(self.nutrition_override.calories)
        if self.food_items and any(
            getattr(item, "nutrition_override", None) is not None
            for item in self.food_items
        ):
            return float(sum(item.calories for item in self.food_items))
        return float(self.macros.total_calories)

    @property
    def effective_macros(self) -> Macros:
        if self.nutrition_override is None:
            return self.macros
        return Macros(
            protein=self.nutrition_override.protein,
            carbs=self.nutrition_override.carbs,
            fat=self.nutrition_override.fat,
            fiber=self.macros.fiber,
            sugar=self.macros.sugar,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        result = {
            "calories": self.calories,
            "macros": self.effective_macros.to_dict(),
            "confidence_score": self.confidence_score,
        }

        if self.micros:
            result["micros"] = self.micros.to_dict()

        if self.food_items:
            result["food_items"] = [item.to_dict() for item in self.food_items]

        if self.nutrition_override:
            result["nutrition_override"] = self.nutrition_override.to_dict()

        return result

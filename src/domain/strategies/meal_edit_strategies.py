"""
Strategy pattern for handling different food item change actions.
Each strategy encapsulates the logic for add, update, or remove operations.
"""

import logging
import uuid
from abc import ABC, abstractmethod

from src.domain.model.meal.food_item_change import FoodItemChange
from src.domain.model.nutrition import FoodItem, Macros, NutritionOverride
from src.domain.services import NutritionCalculationService
from src.domain.services.nutrition_calculation_service import (
    ScaledNutritionResult,
    quantity_to_grams,
    scale_per_100g_nutrition,
)

logger = logging.getLogger(__name__)

MAX_QUANTITY_GRAMS = 10000.0  # 10kg max per food item


def _resolved_nutrition_override(change, existing: FoodItem | None = None):
    if change.clear_nutrition_override:
        return None
    if change.nutrition_override is not None:
        return NutritionOverride(
            calories=change.nutrition_override.calories,
            protein=change.nutrition_override.protein,
            carbs=change.nutrition_override.carbs,
            fat=change.nutrition_override.fat,
        )
    return existing.nutrition_override if existing else None


def _validate_quantity_grams(quantity_grams: float, quantity: float, unit: str) -> None:
    """Raise ValueError if quantity in grams exceeds the realistic limit."""
    if quantity_grams > MAX_QUANTITY_GRAMS:
        raise ValueError(
            f"Quantity {quantity} {unit} ({quantity_grams:.0f}g) exceeds "
            f"maximum allowed ({MAX_QUANTITY_GRAMS:.0f}g)"
        )


class FoodItemChangeStrategy(ABC):
    """Base strategy for applying food item changes."""

    def __init__(self, nutrition_service: NutritionCalculationService):
        self.nutrition_service = nutrition_service

    @abstractmethod
    async def apply(
        self, food_items_dict: dict[str, FoodItem], change: FoodItemChange
    ) -> None:
        """
        Apply the change to the food items dictionary.

        Args:
            food_items_dict: Dictionary of food items (id -> FoodItem)
            change: The change to apply
        """
        pass


class RemoveFoodItemStrategy(FoodItemChangeStrategy):
    """Strategy for removing a food item."""

    async def apply(
        self, food_items_dict: dict[str, FoodItem], change: FoodItemChange
    ) -> None:
        """Remove food item from dictionary."""
        if not change.id:
            logger.warning("Remove action requires id")
            return

        food_items_dict.pop(change.id, None)
        logger.info(f"Removed food item: {change.id}")


class UpdateFoodItemStrategy(FoodItemChangeStrategy):
    """Strategy for updating an existing food item."""

    def __init__(self, nutrition_service, food_reference_repository=None):
        super().__init__(nutrition_service)
        self.food_reference_repository = food_reference_repository

    async def apply(
        self, food_items_dict: dict[str, FoodItem], change: FoodItemChange
    ) -> None:
        """Update existing food item with new quantity/unit or custom nutrition."""
        if not change.id or change.id not in food_items_dict:
            logger.warning(f"Update action requires valid id: {change.id}")
            return

        existing_item = food_items_dict[change.id]
        new_quantity = change.quantity or existing_item.quantity
        new_unit = change.unit or existing_item.unit

        # Priority 1: Custom nutrition provided (user-edited macros)
        if change.custom_nutrition:
            allowed_units = change.allowed_units or existing_item.allowed_units
            quantity_grams = quantity_to_grams(
                new_quantity,
                new_unit,
                existing_item.name,
                allowed_units or [],
                strict=(
                    change.origin is not None
                    or existing_item.nutrition_contract_version == "2"
                ),
            )
            _validate_quantity_grams(quantity_grams, new_quantity, new_unit)
            scale_factor = quantity_grams / 100.0
            food_items_dict[change.id] = FoodItem(
                id=existing_item.id,
                name=existing_item.name,
                quantity=new_quantity,
                unit=new_unit,
                macros=Macros(
                    protein=change.custom_nutrition.protein_per_100g * scale_factor,
                    carbs=change.custom_nutrition.carbs_per_100g * scale_factor,
                    fat=change.custom_nutrition.fat_per_100g * scale_factor,
                    fiber=change.custom_nutrition.fiber_per_100g * scale_factor,
                    sugar=change.custom_nutrition.sugar_per_100g * scale_factor,
                ),
                micros=existing_item.micros,
                confidence=0.8,
                fdc_id=(
                    change.fdc_id if change.origin is not None else existing_item.fdc_id
                ),
                food_reference_id=(
                    change.food_reference_id
                    if change.origin is not None
                    else existing_item.food_reference_id
                ),
                is_custom=(
                    change.origin == "custom" if change.origin is not None else True
                ),
                allowed_units=allowed_units,
                nutrition_override=_resolved_nutrition_override(change, existing_item),
                source_kind=(
                    change.origin
                    if change.origin is not None
                    else existing_item.source_kind
                ),
                source_food_id=(
                    change.source_food_id
                    if change.origin is not None
                    else existing_item.source_food_id
                ),
                nutrition_contract_version=(
                    "2"
                    if change.origin is not None
                    else existing_item.nutrition_contract_version
                ),
                source_snapshot=(
                    change.source_snapshot
                    if change.origin is not None
                    else existing_item.source_snapshot
                ),
            )
            logger.info(
                f"Updated food item with custom nutrition: {existing_item.name}"
            )
            return

        # Priority 2: A unit switch must use the selected unit's canonical
        # source nutrition, rather than rescaling the previously selected unit.
        unit_changed = change.unit and change.unit != existing_item.unit

        if unit_changed:
            scaled_nutrition = await self._get_selected_unit_nutrition(
                existing_item,
                new_quantity,
                new_unit,
            )

            if scaled_nutrition:
                food_items_dict[change.id] = FoodItem(
                    id=existing_item.id,
                    name=existing_item.name,
                    quantity=new_quantity,
                    unit=new_unit,
                    macros=Macros(
                        protein=scaled_nutrition.protein,
                        carbs=scaled_nutrition.carbs,
                        fat=scaled_nutrition.fat,
                    ),
                    micros=existing_item.micros,
                    confidence=0.9,
                    fdc_id=existing_item.fdc_id,
                    food_reference_id=existing_item.food_reference_id,
                    is_custom=existing_item.is_custom,
                    allowed_units=change.allowed_units or existing_item.allowed_units,
                    nutrition_override=_resolved_nutrition_override(
                        change, existing_item
                    ),
                    source_kind=existing_item.source_kind,
                    source_food_id=existing_item.source_food_id,
                    nutrition_contract_version=existing_item.nutrition_contract_version,
                    source_snapshot=existing_item.source_snapshot,
                )
                logger.info(f"Updated food item with unit change: {existing_item.name}")
            else:
                logger.warning(
                    "Could not load canonical nutrition for unit change; "
                    "preserving the existing source nutrition"
                )
                self._preserve_source_nutrition(
                    food_items_dict, change, existing_item, new_quantity, new_unit
                )
        else:
            # Same unit - just scale the nutrition
            self._apply_simple_scaling(
                food_items_dict, change, existing_item, new_quantity, new_unit
            )

    def _apply_simple_scaling(
        self,
        food_items_dict: dict[str, FoodItem],
        change: FoodItemChange,
        existing_item: FoodItem,
        new_quantity: float,
        new_unit: str,
    ) -> None:
        """Apply simple proportional scaling to nutrition with unit conversion."""
        # Convert new quantity to grams for proper scaling
        new_quantity_grams = quantity_to_grams(
            new_quantity,
            new_unit,
            existing_item.name,
            existing_item.allowed_units or [],
        )
        _validate_quantity_grams(new_quantity_grams, new_quantity, new_unit)

        existing_quantity_grams = quantity_to_grams(
            existing_item.quantity,
            existing_item.unit,
            existing_item.name,
            existing_item.allowed_units or [],
        )

        # Scale factor based on gram conversion
        if existing_quantity_grams > 0:
            scale_factor = new_quantity_grams / existing_quantity_grams
        else:
            scale_factor = 1.0

        food_items_dict[change.id] = FoodItem(
            id=existing_item.id,
            name=existing_item.name,
            quantity=new_quantity,
            unit=new_unit,
            macros=Macros(
                protein=existing_item.macros.protein * scale_factor,
                carbs=existing_item.macros.carbs * scale_factor,
                fat=existing_item.macros.fat * scale_factor,
                fiber=existing_item.macros.fiber * scale_factor,
                sugar=existing_item.macros.sugar * scale_factor,
            ),
            micros=existing_item.micros,
            confidence=existing_item.confidence,
            fdc_id=existing_item.fdc_id,
            food_reference_id=existing_item.food_reference_id,
            is_custom=existing_item.is_custom,
            allowed_units=change.allowed_units or existing_item.allowed_units,
            nutrition_override=_resolved_nutrition_override(change, existing_item),
            source_kind=existing_item.source_kind,
            source_food_id=existing_item.source_food_id,
            nutrition_contract_version=existing_item.nutrition_contract_version,
            source_snapshot=existing_item.source_snapshot,
        )
        logger.info(f"Updated food item with scaling: {existing_item.name}")

    async def _get_selected_unit_nutrition(
        self,
        existing_item: FoodItem,
        quantity: float,
        unit: str,
    ) -> ScaledNutritionResult | None:
        snapshot = existing_item.source_snapshot or {}
        if snapshot:
            nutrition = scale_per_100g_nutrition(
                {
                    "protein": snapshot.get("protein_per_100g"),
                    "carbs": snapshot.get("carbs_per_100g"),
                    "fat": snapshot.get("fat_per_100g"),
                    "fiber": snapshot.get("fiber_per_100g", 0),
                    "sugar": snapshot.get("sugar_per_100g", 0),
                },
                quantity,
                unit,
                allowed_units=snapshot.get("allowed_units") or [],
                food_name=existing_item.name,
                strict_allowed_units=existing_item.nutrition_contract_version == "2",
            )
            return ScaledNutritionResult(
                calories=nutrition["calories"],
                protein=nutrition["protein"],
                carbs=nutrition["carbs"],
                fat=nutrition["fat"],
            )

        if existing_item.food_reference_id and self.food_reference_repository:
            reference = await self.food_reference_repository.get_nutrition_projection(
                existing_item.food_reference_id
            )
            if reference is not None and all(
                value is not None
                for value in (
                    reference.protein_100g,
                    reference.carbs_100g,
                    reference.fat_100g,
                )
            ):
                allowed_units = [
                    {
                        "unit": serving.name,
                        "gram_weight": serving.grams,
                        "description": serving.name,
                    }
                    for serving in reference.servings
                    if serving.grams is not None and serving.grams > 0
                ]
                nutrition = scale_per_100g_nutrition(
                    {
                        "protein": reference.protein_100g,
                        "carbs": reference.carbs_100g,
                        "fat": reference.fat_100g,
                        "fiber": reference.fiber_100g,
                        "sugar": reference.sugar_100g,
                    },
                    quantity,
                    unit,
                    allowed_units=allowed_units,
                    food_name=reference.name,
                )
                return ScaledNutritionResult(
                    calories=nutrition["calories"],
                    protein=nutrition["protein"],
                    carbs=nutrition["carbs"],
                    fat=nutrition["fat"],
                )

            # A canonical source was expected but is unavailable. Do not make
            # a new value out of the prior unit's nutrition.
            return None

        return self.nutrition_service.get_nutrition_for_ingredient(
            name=existing_item.name,
            quantity=quantity,
            unit=unit,
            fdc_id=existing_item.fdc_id,
        )

    def _preserve_source_nutrition(
        self,
        food_items_dict: dict[str, FoodItem],
        change: FoodItemChange,
        existing_item: FoodItem,
        quantity: float,
        unit: str,
    ) -> None:
        food_items_dict[change.id] = FoodItem(
            id=existing_item.id,
            name=existing_item.name,
            quantity=quantity,
            unit=unit,
            macros=existing_item.macros,
            micros=existing_item.micros,
            confidence=existing_item.confidence,
            fdc_id=existing_item.fdc_id,
            food_reference_id=existing_item.food_reference_id,
            is_custom=existing_item.is_custom,
            allowed_units=change.allowed_units or existing_item.allowed_units,
            nutrition_override=_resolved_nutrition_override(change, existing_item),
            source_kind=existing_item.source_kind,
            source_food_id=existing_item.source_food_id,
            nutrition_contract_version=existing_item.nutrition_contract_version,
            source_snapshot=existing_item.source_snapshot,
        )


def _resolve_add_item_id(change: FoodItemChange) -> str:
    if not change.id:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(change.id))
    except ValueError as exc:
        raise ValueError("item id must be a valid UUID") from exc


class AddFoodItemStrategy(FoodItemChangeStrategy):
    """Strategy for adding a new food item."""

    def __init__(
        self, nutrition_service: NutritionCalculationService, food_service=None
    ):
        super().__init__(nutrition_service)
        self.food_service = food_service

    async def apply(
        self, food_items_dict: dict[str, FoodItem], change: FoodItemChange
    ) -> None:
        """Add new food item to dictionary."""
        new_item_id = _resolve_add_item_id(change)

        # Try to get nutrition from various sources
        quantity = change.quantity or 100
        unit = change.unit or "g"

        # Priority 1: Custom nutrition provided
        if change.custom_nutrition:
            food_item = self._create_from_custom_nutrition(
                new_item_id,
                change.name or "Custom Ingredient",
                quantity,
                unit,
                change.custom_nutrition,
                change.allowed_units,
                change.nutrition_override,
                change,
            )
            food_items_dict[new_item_id] = food_item
            logger.info(f"Added custom food item: {change.name}")
            return

        # Priority 2: Nutrition service (Pinecone/USDA)
        if change.name:
            scaled_nutrition = self.nutrition_service.get_nutrition_for_ingredient(
                name=change.name, quantity=quantity, unit=unit, fdc_id=change.fdc_id
            )

            if scaled_nutrition:
                food_items_dict[new_item_id] = FoodItem(
                    id=new_item_id,
                    name=change.name,
                    quantity=quantity,
                    unit=unit,
                    macros=Macros(
                        protein=scaled_nutrition.protein,
                        carbs=scaled_nutrition.carbs,
                        fat=scaled_nutrition.fat,
                    ),
                    confidence=0.9,
                    fdc_id=change.fdc_id,
                    food_reference_id=change.food_reference_id,
                    is_custom=False,
                    allowed_units=change.allowed_units,
                    nutrition_override=_resolved_nutrition_override(change),
                    source_kind=change.origin,
                    source_food_id=change.source_food_id,
                    nutrition_contract_version=("2" if change.origin else None),
                    source_snapshot=getattr(change, "source_snapshot", None),
                )
                logger.info(f"Added food item from nutrition service: {change.name}")
                return

        # Priority 3: Fallback — add item with zero macros (never silently discard)
        logger.warning(
            f"No nutrition data found for ingredient: {change.name}, adding with zero macros"
        )
        food_items_dict[new_item_id] = FoodItem(
            id=new_item_id,
            name=change.name or "Unknown Ingredient",
            quantity=quantity,
            unit=unit,
            macros=Macros(protein=0, carbs=0, fat=0),
            confidence=0.3,
            fdc_id=change.fdc_id,
            food_reference_id=change.food_reference_id,
            is_custom=True,
            allowed_units=change.allowed_units,
            nutrition_override=_resolved_nutrition_override(change),
            source_kind=change.origin or "custom",
            source_food_id=change.source_food_id,
            nutrition_contract_version=("2" if change.origin else None),
            source_snapshot=getattr(change, "source_snapshot", None),
        )

    def _create_from_custom_nutrition(
        self,
        item_id: str,
        name: str,
        quantity: float,
        unit: str,
        custom_nutrition,
        allowed_units=None,
        nutrition_override=None,
        change=None,
    ) -> FoodItem:
        """Create food item from custom nutrition data."""
        quantity_grams = quantity_to_grams(
            quantity,
            unit,
            name,
            allowed_units or [],
            strict=change is not None and change.origin is not None,
        )
        _validate_quantity_grams(quantity_grams, quantity, unit)
        scale_factor = quantity_grams / 100.0  # Custom nutrition is per 100g

        return FoodItem(
            id=item_id,
            name=name,
            quantity=quantity,
            unit=unit,
            macros=Macros(
                protein=custom_nutrition.protein_per_100g * scale_factor,
                carbs=custom_nutrition.carbs_per_100g * scale_factor,
                fat=custom_nutrition.fat_per_100g * scale_factor,
                fiber=custom_nutrition.fiber_per_100g * scale_factor,
                sugar=custom_nutrition.sugar_per_100g * scale_factor,
            ),
            confidence=0.8,
            fdc_id=change.fdc_id if change else None,
            food_reference_id=(change.food_reference_id if change else None),
            is_custom=True,
            allowed_units=allowed_units,
            nutrition_override=(
                NutritionOverride(
                    calories=nutrition_override.calories,
                    protein=nutrition_override.protein,
                    carbs=nutrition_override.carbs,
                    fat=nutrition_override.fat,
                )
                if nutrition_override
                else None
            ),
            source_kind=(change.origin if change and change.origin else "custom"),
            source_food_id=change.source_food_id if change else None,
            nutrition_contract_version=("2" if change and change.origin else None),
            source_snapshot=getattr(change, "source_snapshot", None),
        )


class FoodItemChangeStrategyFactory:
    """Factory for creating appropriate strategy based on action."""

    @staticmethod
    def create_strategies(
        nutrition_service: NutritionCalculationService,
        food_service=None,
        food_reference_repository=None,
    ) -> dict[str, FoodItemChangeStrategy]:
        """Create all available strategies."""
        return {
            "add": AddFoodItemStrategy(nutrition_service, food_service),
            "update": UpdateFoodItemStrategy(
                nutrition_service,
                food_reference_repository,
            ),
            "remove": RemoveFoodItemStrategy(nutrition_service),
        }

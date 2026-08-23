"""
Nutrition calculation service - domain service for nutrition-related operations.
Provides a unified interface for calculating nutrition from various sources.
"""

import logging
from dataclasses import dataclass
from typing import Any

from src.domain.constants.food_density import DEFAULT_DENSITY, get_density
from src.domain.model.nutrition import FoodItem, Macros, Nutrition

logger = logging.getLogger(__name__)


# Shared unit-to-grams conversion table for common serving units.
# Used by both parse-text and manual-meal handlers to ensure consistent nutrition.
UNIT_TO_GRAMS = {
    "large": 50.0,  # ~50g per large egg
    "medium": 44.0,  # ~44g per medium egg
    "small": 38.0,  # ~38g per small egg
    "cup": 240.0,
    "tablespoon": 15.0,
    "tbsp": 15.0,
    "teaspoon": 5.0,
    "tsp": 5.0,
    "piece": 100.0,
    "slice": 30.0,
    "serving": 100.0,
    "kg": 1000.0,
    "lb": 453.6,
    "oz": 28.35,
    # ml/l removed — handled by density-aware logic in convert_quantity_to_grams
}

# Safety-net translation for when AI ignores the English-unit prompt instruction.
# Not exhaustive — primary fix is the dual-unit approach (unit + english_unit).
# Unknown units use the safe global fallback in convert_quantity_to_grams.
UNIT_TRANSLATION = {
    # English long-form weight/volume units
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "pound": "lb",
    "pounds": "lb",
    "ounce": "oz",
    "ounces": "oz",
    "gramme": "g",
    "grammes": "g",
    # Vietnamese count/size units
    "quả lớn": "large",
    "quả to": "large",
    "trái lớn": "large",
    "quả vừa": "medium",
    "trái vừa": "medium",
    "quả nhỏ": "small",
    "trái nhỏ": "small",
    "quả": "piece",
    "trái": "piece",
    "cái": "piece",
    "miếng": "piece",
    "lát": "slice",
    "khúc": "piece",
    "tô": "cup",
    "chén": "cup",
    "bát": "cup",
    "muỗng canh": "tablespoon",
    "muỗng súp": "tablespoon",
    "thìa canh": "tablespoon",
    "muỗng cà phê": "teaspoon",
    "thìa cà phê": "teaspoon",
    "phần": "serving",
    "suất": "serving",
    "khẩu phần": "serving",
    "nhánh": "serving",
    "sprig": "serving",
    "sprigs": "serving",
    "cọng": "serving",
    "lá": "piece",
    "ổ": "piece",
    "loaf": "piece",
    "ít": "serving",
    "một ít": "serving",
    "chút": "serving",
    "chut": "serving",
    "chút ít": "serving",
    "chút xíu": "serving",
    "vài": "serving",
    "nắm": "serving",
    "nhiều": "serving",
    "pinch": "serving",
    "pinches": "serving",
    "dash": "serving",
    "dashes": "serving",
    "handful": "serving",
    "a little": "serving",
    # Spanish
    "grande": "large",
    "mediano": "medium",
    "pequeño": "small",
    "pieza": "piece",
    "rebanada": "slice",
    "taza": "cup",
    "cucharada": "tablespoon",
    "cucharadita": "teaspoon",
    "porción": "serving",
    # French
    "gros": "large",
    "moyen": "medium",
    "petit": "small",
    "morceau": "piece",
    "tranche": "slice",
    "tasse": "cup",
    "cuillère à soupe": "tablespoon",
    "cuillère à café": "teaspoon",
    "portion": "serving",
    # Japanese
    "個": "piece",
    "枚": "slice",
    "杯": "cup",
    "大さじ": "tablespoon",
    "小さじ": "teaspoon",
    # Chinese
    "大": "large",
    "中": "medium",
    "小": "small",
    "块": "piece",
    "片": "slice",
    "汤匙": "tablespoon",
    "茶匙": "teaspoon",
    "份": "serving",
}

CONVERTIBLE_UNITS = set(UNIT_TO_GRAMS) | {"g", "ml", "l", "liter", "litre"}
MASS_VOLUME_CANONICAL_UNITS = {"g", "kg", "ml", "l", "oz", "lb"}


def _normalize_unit(unit: str) -> str:
    """Normalize unit string: translate multilingual, strip qualifiers, handle plurals."""
    unit = (unit or "g").lower().strip()
    # Translate multilingual units to English (check full string first)
    if unit in UNIT_TRANSLATION:
        return UNIT_TRANSLATION[unit]
    # Strip common qualifiers (e.g., "cup cooked" → "cup", "medium ripe" → "medium")
    base = unit.split()[0] if " " in unit else unit
    # Check translation again after stripping qualifier
    if base in UNIT_TRANSLATION:
        return UNIT_TRANSLATION[base]
    # Handle plurals (e.g., "tablespoons" → "tablespoon", "cups" → "cup")
    if base.endswith("s") and base not in UNIT_TO_GRAMS:
        singular = base[:-1]
        if singular in UNIT_TO_GRAMS:
            return singular
    return base


def canonicalize_mass_volume_unit(unit: str | None) -> str:
    """Collapse gram/ounce/liter aliases onto the canonical mass-volume token."""
    raw = (unit or "g").strip() or "g"
    normalized = _normalize_unit(raw)
    if normalized in MASS_VOLUME_CANONICAL_UNITS:
        return normalized
    return raw


def normalize_unit_for_manual_save(unit: str | None) -> str:
    """Return a client-safe unit accepted by manual meal creation."""
    normalized = _normalize_unit(unit or "")
    if normalized in CONVERTIBLE_UNITS:
        return normalized

    logger.warning(
        f"Unknown parse-text unit '{unit}' (normalized: '{normalized}') - "
        "returning 'serving' for manual-save compatibility"
    )
    return "serving"


def convert_quantity_to_grams(quantity: float, unit: str, food_name: str = "") -> float:
    """Convert a quantity+unit pair to grams.

    For volume units (ml, l), applies food-specific density from
    ``food_density.get_density``.  Weight/count units use the global
    ``UNIT_TO_GRAMS`` mapping.
    """
    normalized = _normalize_unit(unit)
    if normalized == "g":
        return quantity

    # Volume units — apply density
    if normalized in ("ml", "l", "liter", "litre"):
        base_ml = quantity if normalized == "ml" else quantity * 1000
        density = get_density(food_name) if food_name else DEFAULT_DENSITY
        return base_ml * density

    grams_per_unit = UNIT_TO_GRAMS.get(normalized)
    if grams_per_unit is None:
        logger.warning("Unknown unit used safe 100g fallback")
        return quantity * 100.0
    return quantity * grams_per_unit


def quantity_to_grams(
    quantity: float,
    unit: str,
    food_name: str = "",
    serving_options: list[dict] | None = None,
    *,
    strict: bool = False,
) -> float:
    """Convert quantity and unit using source servings when available."""
    if serving_options:
        return _convert_with_serving_options(
            quantity, unit, serving_options, food_name, strict=strict
        )
    return convert_quantity_to_grams(quantity, unit, food_name)


def _convert_with_serving_options(
    quantity: float,
    unit: str,
    serving_options: list[dict] | None = None,
    food_name: str = "",
    *,
    strict: bool = False,
) -> float:
    """Convert using a provider's serving options, then global unit mappings."""
    normalized = _normalize_unit(unit)
    if normalized == "g":
        return quantity

    for option in serving_options or []:
        option_unit = str(option.get("unit") or "")
        gram_weight = float(option.get("gram_weight") or 0)
        if not _units_refer_to_same_serving(option_unit, unit):
            continue
        if not _has_trusted_portion_weight(option_unit, gram_weight):
            continue
        logger.debug("Matched source serving option for unit=%s", unit)
        return quantity * gram_weight

    if strict:
        raise ValueError("unit is not present in the supplied serving options")

    grams = convert_quantity_to_grams(quantity, unit, food_name)
    if not is_unknown_unit(unit):
        return grams

    for option in serving_options or []:
        option_unit = str(option.get("unit") or "")
        gram_weight = float(option.get("gram_weight") or 0)
        if _normalize_unit(option_unit) == "g" or not _has_trusted_portion_weight(
            option_unit, gram_weight
        ):
            continue
        return quantity * gram_weight
    return grams


def is_unknown_unit(unit: str) -> bool:
    normalized = _normalize_unit(unit)
    return normalized not in UNIT_TO_GRAMS and normalized not in {
        "g",
        "ml",
        "l",
        "liter",
        "litre",
    }


def _has_trusted_portion_weight(unit: str, gram_weight: float) -> bool:
    return gram_weight > 0 if _normalize_unit(unit) in {"g", "ml"} else gram_weight > 1


def _units_refer_to_same_serving(left: str, right: str) -> bool:
    a = left.strip().lower()
    b = right.strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    return _normalize_unit(a) == _normalize_unit(b)


MAX_KCAL_PER_100G = 900.0
_ENERGY_MISMATCH_RATIO = 5.0


def reconcile_calories_per_100g(advertised: float, derived: float) -> float:
    """Keep advertised energy only when it is physically plausible vs macros."""
    if derived <= 0:
        return max(advertised, 0.0)
    if advertised < 0 or advertised > MAX_KCAL_PER_100G:
        return derived
    if advertised > derived * _ENERGY_MISMATCH_RATIO:
        return derived
    if derived > advertised * _ENERGY_MISMATCH_RATIO:
        return derived
    return advertised


def scale_per_100g_nutrition(
    per_100g: dict,
    quantity: float,
    unit: str,
    base_serving: float = 100.0,
    serving_options: list[dict] | None = None,
    food_name: str = "",
    strict_serving_options: bool = False,
) -> dict:
    """Scale per-100g nutrition values for a given quantity and unit.

    Args:
        per_100g: Dict with per-100g nutrition values (calories, protein, carbs, fat)
        quantity: The quantity amount
        unit: The unit name (e.g., "cup", "g", "piece")
        base_serving: The base serving size in grams (default 100g)
        food_name: Food name for density-aware ml→g conversion

    Returns:
        dict with keys: calories, protein, carbs, fat.
    """
    quantity_in_grams = quantity_to_grams(
        quantity,
        unit,
        food_name,
        serving_options,
        strict=strict_serving_options,
    )

    factor = (quantity_in_grams / base_serving) if base_serving > 0 else 0.0
    return {
        "calories": round(per_100g.get("calories", 0.0) * factor, 2),
        "protein": round(per_100g.get("protein", 0.0) * factor, 2),
        "carbs": round(per_100g.get("carbs", 0.0) * factor, 2),
        "fat": round(per_100g.get("fat", 0.0) * factor, 2),
        "fiber": round(per_100g.get("fiber", 0.0) * factor, 2),
        "sugar": round(per_100g.get("sugar", 0.0) * factor, 2),
    }


def clamp_nutrition_values(item: dict) -> dict:
    """Clamp nutrition to physically plausible ranges for the given quantity.

    Macronutrients (protein/carbs/fat) cannot exceed the food's total weight.
    Returns clamped values; logs a warning when clamping occurs.
    """
    quantity = item.get("quantity", 1.0)
    unit = normalize_unit_for_manual_save(item.get("english_unit") or item.get("unit"))

    # Estimate weight in grams for plausibility check
    food_name = item.get("name", "")
    weight_g = convert_quantity_to_grams(quantity, unit, food_name)
    if weight_g <= 0:
        return item

    calories = item.get("calories") or 0.0
    protein = item.get("protein") or 0.0
    carbs = item.get("carbs") or 0.0
    fat = item.get("fat") or 0.0

    # Each macro can't exceed total weight; calories max ~9 kcal/g (pure fat)
    max_cal = weight_g * 9.0
    clamped = {
        "calories": min(max(calories, 0), max_cal),
        "protein": min(max(protein, 0), weight_g),
        "carbs": min(max(carbs, 0), weight_g),
        "fat": min(max(fat, 0), weight_g),
    }

    if clamped != {
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
    }:
        logger.warning(
            f"Clamped implausible nutrition for '{item.get('name', '?')}' "
            f"({quantity} {unit}): {calories:.1f}cal/{protein:.1f}p/{carbs:.1f}c/{fat:.1f}f "
            f"-> {clamped['calories']:.1f}cal/{clamped['protein']:.1f}p/"
            f"{clamped['carbs']:.1f}c/{clamped['fat']:.1f}f"
        )

    return clamped


@dataclass
class ScaledNutritionResult:
    """Result of nutrition calculation for a specific quantity."""

    calories: float
    protein: float
    carbs: float
    fat: float


class NutritionCalculationService:
    """
    Domain service for calculating nutrition from various sources.

    Provides a single source of truth for nutrition calculations, with
    fallback mechanisms for robustness.
    """

    def __init__(self):
        pass

    def get_nutrition_for_ingredient(
        self, name: str, quantity: float, unit: str, fdc_id: int | None = None
    ) -> ScaledNutritionResult | None:
        """
        Get nutrition data for an ingredient.
        Currently returns None — vector search removed, to be re-added later.
        """
        logger.warning(
            f"Could not find nutrition data for '{name}' — no vector search configured"
        )
        return None

    def calculate_meal_total(self, food_items: list[FoodItem]) -> Nutrition:
        """
        Calculate total nutrition from a list of food items.

        Args:
            food_items: List of food items in the meal

        Returns:
            Nutrition object with totals
        """
        if not food_items:
            return Nutrition(
                macros=Macros(protein=0, carbs=0, fat=0),
                food_items=[],
                confidence_score=1.0,
            )

        total_protein = sum(item.effective_macros.protein for item in food_items)
        total_carbs = sum(item.effective_macros.carbs for item in food_items)
        total_fat = sum(item.effective_macros.fat for item in food_items)
        total_fiber = sum(item.effective_macros.fiber for item in food_items)
        total_sugar = sum(item.effective_macros.sugar for item in food_items)

        # Calculate average confidence
        avg_confidence = sum(item.confidence for item in food_items) / len(food_items)

        return Nutrition(
            macros=Macros(
                protein=total_protein,
                carbs=total_carbs,
                fat=total_fat,
                fiber=total_fiber,
                sugar=total_sugar,
            ),
            food_items=food_items,
            confidence_score=avg_confidence,
        )

    def aggregate_from_command_items(self, items) -> tuple:
        """
        Aggregate nutrition from command items with custom_nutrition.
        Returns (Nutrition, list[FoodItem]). Items without custom_nutrition are skipped.
        """
        from uuid import uuid4

        food_items = []
        total_protein = total_carbs = total_fat = 0.0
        total_fiber = total_sugar = 0.0

        for item in items:
            if not item.custom_nutrition:
                continue
            item_name = item.name or "Food Item"
            quantity_grams = convert_quantity_to_grams(
                item.quantity,
                item.unit,
                item_name,
            )
            if getattr(item, "serving_options", None):
                quantity_grams = quantity_to_grams(
                    item.quantity,
                    item.unit,
                    item_name,
                    item.serving_options,
                )
            factor = quantity_grams / 100.0
            protein = item.custom_nutrition.protein_per_100g * factor
            carbs = item.custom_nutrition.carbs_per_100g * factor
            fat = item.custom_nutrition.fat_per_100g * factor
            fiber = item.custom_nutrition.fiber_per_100g * factor
            sugar = item.custom_nutrition.sugar_per_100g * factor
            total_protein += protein
            total_carbs += carbs
            total_fat += fat
            total_fiber += fiber
            total_sugar += sugar
            food_items.append(
                FoodItem(
                    id=uuid4(),
                    name=item_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    macros=Macros(
                        protein=protein,
                        carbs=carbs,
                        fat=fat,
                        fiber=fiber,
                        sugar=sugar,
                    ),
                    micros=None,
                    confidence=1.0,
                    fdc_id=getattr(item, "fdc_id", None),
                    food_reference_id=getattr(item, "food_reference_id", None),
                    is_custom=getattr(item, "origin", None) == "custom",
                    serving_options=getattr(item, "serving_options", None),
                    source_kind=getattr(item, "source_kind", None),
                    source_food_id=getattr(item, "source_food_id", None),
                    nutrition_contract_version=getattr(
                        item, "nutrition_contract_version", None
                    ),
                    source_snapshot=getattr(item, "source_snapshot", None),
                )
            )

        nutrition = Nutrition(
            macros=Macros(
                protein=round(total_protein, 1),
                carbs=round(total_carbs, 1),
                fat=round(total_fat, 1),
                fiber=round(total_fiber, 1),
                sugar=round(total_sugar, 1),
            ),
            food_items=food_items,
            confidence_score=1.0,
        )
        return nutrition, food_items

    def scale_nutrition(
        self,
        original_nutrition: ScaledNutritionResult,
        original_quantity: float,
        new_quantity: float,
    ) -> ScaledNutritionResult:
        """
        Scale nutrition proportionally based on quantity change.

        Args:
            original_nutrition: Original nutrition values
            original_quantity: Original quantity
            new_quantity: New quantity

        Returns:
            Scaled nutrition values
        """
        if original_quantity <= 0:
            raise ValueError(f"Original quantity must be positive: {original_quantity}")

        scale_factor = new_quantity / original_quantity

        return ScaledNutritionResult(
            calories=original_nutrition.calories * scale_factor,
            protein=original_nutrition.protein * scale_factor,
            carbs=original_nutrition.carbs * scale_factor,
            fat=original_nutrition.fat * scale_factor,
        )

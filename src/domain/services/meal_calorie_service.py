"""Meal calorie helpers for source-specific display and summary totals."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from src.domain.model.nutrition.macros import Macros

# Reject absurd upward jumps unless the correction clearly asks for more food.
MAX_CALORIE_MULTIPLIER = 8.0
MAX_ABSOLUTE_CALORIES = 10_000.0

_LARGE_PORTION_HINT = re.compile(
    r"\b("
    r"double|triple|extra|more|bigger|large|larger|whole|"
    r"twice|x2|2x|all of|full portion|add(?:ed|ing)?|"
    r"gấp đôi|gấp ba|thêm|nhiều hơn|toàn bộ"
    r")\b",
    re.IGNORECASE,
)


def effective_meal_calories(meal: Any) -> float:
    """Return calories users expect for a meal.

    Food-label scans should match the printed label calories. Other meal
    sources keep the canonical macro-derived value, unless a meal-level
    override is set or ingredient calorie overrides require summing items.
    """
    from src.domain.model.nutrition.nutrition import Nutrition, NutritionOverride

    nutrition = getattr(meal, "nutrition", None)
    if nutrition is None:
        return 0.0

    override = getattr(nutrition, "nutrition_override", None)
    if isinstance(override, NutritionOverride):
        return float(override.calories)

    if getattr(meal, "source", None) == "food_label":
        metadata = getattr(meal, "food_label_metadata", None)
        label_calories = _food_label_calories(metadata)
        if label_calories is not None:
            item = (getattr(nutrition, "food_items", None) or [None])[0]
            return _scaled_label_calories(label_calories, item, metadata)

    # Real Nutrition VOs may include per-item calorie overrides in .calories.
    if isinstance(nutrition, Nutrition):
        return float(nutrition.calories)

    return _macro_calories(getattr(nutrition, "macros", None))


def effective_food_item_calories(
    item: Any,
    *,
    meal_source: str | None = None,
    food_label_metadata: dict[str, Any] | None = None,
) -> float:
    """Return item calories, preserving printed calories for label scans."""
    if meal_source == "food_label":
        label_calories = _food_label_calories(food_label_metadata)
        if label_calories is not None:
            return _scaled_label_calories(label_calories, item, food_label_metadata)

    return float(getattr(item, "calories", 0.0) or 0.0)


def _macro_calories(macros: Any) -> float:
    if macros is None:
        return 0.0

    protein = _safe_number(getattr(macros, "protein", 0.0))
    carbs = _safe_number(getattr(macros, "carbs", 0.0))
    fat = _safe_number(getattr(macros, "fat", 0.0))
    fiber = _safe_number(getattr(macros, "fiber", 0.0))
    return round(Macros.raw_total_calories(protein, carbs, fat, fiber), 1)


def _safe_number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _food_label_calories(metadata: dict[str, Any] | None) -> float | None:
    if not isinstance(metadata, dict):
        return None

    value = metadata.get("label_calories_per_serving")
    if value is None:
        return None

    try:
        calories = float(value)
    except (TypeError, ValueError):
        return None

    return calories if calories >= 0 else None


def _scaled_label_calories(
    label_calories: float,
    item: Any,
    metadata: dict[str, Any] | None,
) -> float:
    serving_size = metadata.get("serving_size") if isinstance(metadata, dict) else None
    serving_grams = None
    if isinstance(serving_size, dict):
        try:
            serving_grams = float(serving_size.get("grams") or 0)
        except (TypeError, ValueError):
            serving_grams = None

    quantity = getattr(item, "quantity", None)
    unit = str(getattr(item, "unit", "") or "").strip().lower()
    if item is None or unit not in {"g", "gram", "grams"}:
        return round(label_calories, 1)

    try:
        quantity_grams = float(quantity)
    except (TypeError, ValueError):
        return round(label_calories, 1)

    if not serving_grams or serving_grams <= 0 or quantity_grams <= 0:
        return round(label_calories, 1)

    return round(label_calories * (quantity_grams / serving_grams), 1)


def normalize_food_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().casefold())


def _name_mentioned(name: str, correction_lower: str) -> bool:
    token = normalize_food_name(name)
    if not token or len(token) < 2:
        return False
    if token in correction_lower:
        return True
    # Match multi-word names when any significant word appears.
    parts = [part for part in re.split(r"[^a-z0-9à-ỹ]+", token) if len(part) >= 4]
    return any(part in correction_lower for part in parts)


def merge_fix_result_items(
    old_items: Iterable[Any],
    new_items: Iterable[Any],
    correction: str,
) -> list[Any]:
    """Keep old items the model forgot, without duplicating renamed ones.

    Matched names keep the new estimate (portion/identity may have changed) but
    reuse the old id so clients can track the same row. Calories always come
    from macros via FoodItem.calories / effective_food_item_calories, never a
    client override on this path.
    """
    from src.domain.model.nutrition import FoodItem

    old_list = list(old_items or [])
    new_list = list(new_items or [])
    correction_lower = (correction or "").casefold()

    old_by_name: dict[str, Any] = {}
    for item in old_list:
        key = normalize_food_name(item.name)
        if key and key not in old_by_name:
            old_by_name[key] = item

    merged: list[Any] = []
    seen_old: set[str] = set()
    for item in new_list:
        key = normalize_food_name(item.name)
        previous = old_by_name.get(key)
        if previous is not None:
            seen_old.add(key)
            # Preserve stable id; nutrition already macro-derived.
            merged.append(
                FoodItem(
                    id=previous.id,
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                    macros=item.macros,
                    micros=item.micros,
                    confidence=item.confidence,
                    fdc_id=item.fdc_id or previous.fdc_id,
                    food_reference_id=item.food_reference_id
                    or previous.food_reference_id,
                    is_custom=item.is_custom,
                    allowed_units=item.allowed_units or previous.allowed_units,
                    nutrition_override=None,
                    source_kind=item.source_kind or previous.source_kind,
                    source_food_id=item.source_food_id or previous.source_food_id,
                    nutrition_contract_version=item.nutrition_contract_version
                    or previous.nutrition_contract_version,
                    source_snapshot=item.source_snapshot or previous.source_snapshot,
                )
            )
            # Touch calories through the shared helper so rename/portion
            # changes stay on the backend formula path.
            _ = effective_food_item_calories(merged[-1])
        else:
            merged.append(item)
            _ = effective_food_item_calories(merged[-1])

    added = [
        item for item in new_list if normalize_food_name(item.name) not in old_by_name
    ]
    missing = [old for old in old_list if normalize_food_name(old.name) not in seen_old]
    # A missing item the correction names was removed or replaced on purpose.
    unnamed_missing = [
        old for old in missing if not _name_mentioned(old.name, correction_lower)
    ]
    # The model returns the complete plate, so each added name replaces one
    # missing item: "fish not chicken" pairs the named ones, and any other
    # added name is a rename (scans store localized names, the model answers
    # in English). Unnamed items left over were forgotten, so keep them.
    named_added = sum(_name_mentioned(item.name, correction_lower) for item in added)
    paired_by_name = min(named_added, len(missing) - len(unnamed_missing))
    merged.extend(unnamed_missing[len(added) - paired_by_name :])

    return merged


def assert_fix_result_calorie_guardrail(
    *,
    old_calories: float,
    new_calories: float,
    correction: str,
) -> None:
    """Raise ValueError when the AI rewrite is an absurd calorie jump."""
    old_value = max(0.0, float(old_calories or 0.0))
    new_value = max(0.0, float(new_calories or 0.0))

    if new_value <= 0:
        raise ValueError("FIX_RESULT_EMPTY_CALORIES")

    if new_value > MAX_ABSOLUTE_CALORIES and not _LARGE_PORTION_HINT.search(
        correction or ""
    ):
        raise ValueError("FIX_RESULT_CALORIE_JUMP")

    if old_value <= 0:
        return

    if (
        new_value > old_value * MAX_CALORIE_MULTIPLIER
        and not _LARGE_PORTION_HINT.search(correction or "")
    ):
        raise ValueError("FIX_RESULT_CALORIE_JUMP")


def serialize_items_for_fix_prompt(items: Iterable[Any]) -> list[dict]:
    """Compact current-item context for the vision correction prompt."""
    payload: list[dict] = []
    for item in items or []:
        macros = item.effective_macros
        payload.append(
            {
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "calories": round(float(item.calories), 1),
                "protein": round(float(macros.protein), 1),
                "carbs": round(float(macros.carbs), 1),
                "fat": round(float(macros.fat), 1),
                "confidence": round(float(item.confidence), 3),
            }
        )
    return payload

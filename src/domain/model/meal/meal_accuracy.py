"""Meal-detail accuracy fields derived from data already stored on the meal.

No I/O. Item nutrition origin stays on each food item as ``source_kind``
(API field ``origin``: local, usda, provider, or custom). These helpers
do not add a meal-level origin.
"""

from __future__ import annotations

from typing import Any, TypedDict

# Meal.source values produced by an AI write path.
AI_GENERATED_MEAL_SOURCES = frozenset(
    {"scanner", "food_label", "prompt", "ai_suggestion"}
)


class NutritionSourceCounts(TypedDict):
    local: int
    usda: int
    provider: int
    custom: int
    unknown: int


class MealAccuracyFields(TypedDict):
    ai_generated: bool
    ai_confidence: float | None
    nutrition_source_summary: NutritionSourceCounts


def meal_accuracy_fields(meal: Any) -> MealAccuracyFields:
    """Fields added to the detailed meal response."""
    source = getattr(meal, "source", None)
    return {
        "ai_generated": is_ai_generated_meal_source(source),
        "ai_confidence": scan_confidence(meal),
        "nutrition_source_summary": nutrition_source_summary(meal),
    }


def is_ai_generated_meal_source(source: str | None) -> bool:
    """True when ``Meal.source`` is one of the AI write paths."""
    return source in AI_GENERATED_MEAL_SOURCES


def scan_confidence(meal: Any) -> float | None:
    """Vision confidence stored on the meal, only for a scanner meal.

    Other sources keep their own confidence numbers (food-label metadata,
    the Nutrition default of 1.0) and must not surface them here. A scanner
    meal with no nutrition yet has no scan confidence.
    """
    if getattr(meal, "source", None) != "scanner":
        return None
    nutrition = getattr(meal, "nutrition", None)
    if nutrition is None:
        return None
    score = getattr(nutrition, "confidence_score", None)
    if score is None:
        return None
    return float(score)


def nutrition_source_summary(meal: Any) -> NutritionSourceCounts:
    """Count food items by the origin already returned on each item.

    Missing or unrecognized ``source_kind`` counts as ``unknown``. Photo
    scans persist items without a logical origin, so those meals count
    as unknown until an edit stores one.
    """
    counts: NutritionSourceCounts = {
        "local": 0,
        "usda": 0,
        "provider": 0,
        "custom": 0,
        "unknown": 0,
    }
    nutrition = getattr(meal, "nutrition", None)
    items = getattr(nutrition, "food_items", None) or []
    for item in items:
        kind = getattr(item, "source_kind", None)
        if kind == "local":
            counts["local"] += 1
        elif kind == "usda":
            counts["usda"] += 1
        elif kind == "provider":
            counts["provider"] += 1
        elif kind == "custom":
            counts["custom"] += 1
        else:
            counts["unknown"] += 1
    return counts

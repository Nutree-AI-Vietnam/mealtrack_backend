"""Non-persisted meal content fingerprinting and deduplication."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.domain.model.meal import Meal


def compute_meal_content_fingerprint(meal: Meal) -> str:
    """Generate deterministic hash representing repeatable food content of a meal.

    Includes normalized dish name, ingredient canonical identities/names,
    quantities, units, macros, and nutrition overrides.
    Excludes meal IDs, timestamps, images, translations, and source metadata.
    """
    dish_name = (meal.dish_name or "").strip().lower()

    items_data: list[dict[str, Any]] = []
    food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
    for item in food_items:
        item_name = (getattr(item, "name", "") or "").strip().lower()
        food_ref_id = getattr(item, "food_reference_id", None)
        source_food_id = getattr(item, "source_food_id", None)
        quantity = round(float(getattr(item, "quantity", 0.0) or 0.0), 3)
        unit = (getattr(item, "unit", "") or "").strip().lower()

        # Macros if present
        macros = getattr(item, "macros", None)
        macro_dict = None
        if macros:
            macro_dict = {
                "protein": round(float(getattr(macros, "protein", 0.0) or 0.0), 1),
                "carbs": round(float(getattr(macros, "carbs", 0.0) or 0.0), 1),
                "fat": round(float(getattr(macros, "fat", 0.0) or 0.0), 1),
                "fiber": round(float(getattr(macros, "fiber", 0.0) or 0.0), 1),
                "sugar": round(float(getattr(macros, "sugar", 0.0) or 0.0), 1),
            }

        # Custom nutrition / override
        override = getattr(item, "nutrition_override", None)
        override_dict = None
        if override:
            override_dict = {
                "calories": round(float(getattr(override, "calories", 0.0) or 0.0), 1),
                "protein": round(float(getattr(override, "protein", 0.0) or 0.0), 1),
                "carbs": round(float(getattr(override, "carbs", 0.0) or 0.0), 1),
                "fat": round(float(getattr(override, "fat", 0.0) or 0.0), 1),
            }

        items_data.append(
            {
                "name": item_name,
                "food_reference_id": food_ref_id,
                "source_food_id": source_food_id,
                "quantity": quantity,
                "unit": unit,
                "macros": macro_dict,
                "override": override_dict,
            }
        )

    # Sort items deterministically
    items_data.sort(
        key=lambda x: (
            x["name"],
            str(x["food_reference_id"]),
            str(x["source_food_id"]),
            x["quantity"],
            x["unit"],
        )
    )

    meal_macros = getattr(getattr(meal, "nutrition", None), "macros", None)
    meal_macro_dict = None
    if meal_macros:
        meal_macro_dict = {
            "protein": round(float(getattr(meal_macros, "protein", 0.0) or 0.0), 1),
            "carbs": round(float(getattr(meal_macros, "carbs", 0.0) or 0.0), 1),
            "fat": round(float(getattr(meal_macros, "fat", 0.0) or 0.0), 1),
            "fiber": round(float(getattr(meal_macros, "fiber", 0.0) or 0.0), 1),
            "sugar": round(float(getattr(meal_macros, "sugar", 0.0) or 0.0), 1),
        }

    meal_override = getattr(
        getattr(meal, "nutrition", None), "nutrition_override", None
    )
    meal_override_dict = None
    if meal_override:
        meal_override_dict = {
            "calories": round(float(getattr(meal_override, "calories", 0.0) or 0.0), 1),
            "protein": round(float(getattr(meal_override, "protein", 0.0) or 0.0), 1),
            "carbs": round(float(getattr(meal_override, "carbs", 0.0) or 0.0), 1),
            "fat": round(float(getattr(meal_override, "fat", 0.0) or 0.0), 1),
        }

    payload = {
        "dish_name": dish_name,
        "items": items_data,
        "macros": meal_macro_dict,
        "override": meal_override_dict,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def deduplicate_recent_meals(meals: list[Meal], limit: int = 20) -> list[Meal]:
    """Deduplicate recent meals by repeatable content fingerprint, preserving newest first."""
    seen_fingerprints: set[str] = set()
    unique_meals: list[Meal] = []
    for meal in meals:
        fp = compute_meal_content_fingerprint(meal)
        if fp not in seen_fingerprints:
            seen_fingerprints.add(fp)
            unique_meals.append(meal)
            if len(unique_meals) >= limit:
                break
    return unique_meals

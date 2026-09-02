"""Non-persisted meal identity fingerprinting and deduplication.

Meal identity rule (NM-437 / NM-438): two meals are the SAME meal when they
have the same set of food items and the same grams (quantity + unit) for each
item. Different items or different grams means a different meal. Dish name,
macro estimates, and nutrition overrides are NOT part of the identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.domain.model.meal import Meal


def compute_meal_content_fingerprint(meal: Meal) -> str:
    """Generate deterministic hash representing the identity of a meal.

    Identity = the set of food items (what they are) plus their quantities
    and units. Excludes meal IDs, timestamps, images, translations, dish
    name, macro estimates, and nutrition overrides.

    Meals without any food items fall back to dish name + total macros so
    that distinct item-less meals do not collapse into one identity.
    """
    items_data: list[dict[str, Any]] = []
    food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
    for item in food_items:
        items_data.append(
            {
                "name": (getattr(item, "name", "") or "").strip().lower(),
                "food_reference_id": getattr(item, "food_reference_id", None),
                "source_food_id": getattr(item, "source_food_id", None),
                "quantity": round(float(getattr(item, "quantity", 0.0) or 0.0), 3),
                "unit": (getattr(item, "unit", "") or "").strip().lower(),
            }
        )

    # Sort items deterministically: identity is a SET of items, not a sequence
    items_data.sort(
        key=lambda x: (
            x["name"],
            str(x["food_reference_id"]),
            str(x["source_food_id"]),
            x["quantity"],
            x["unit"],
        )
    )

    if items_data:
        payload: dict[str, Any] = {"items": items_data}
    else:
        # Fallback identity for meals without itemized food content
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
        payload = {
            "dish_name": (meal.dish_name or "").strip().lower(),
            "macros": meal_macro_dict,
        }

    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def deduplicate_recent_meals(meals: list[Meal], limit: int = 10) -> list[Meal]:
    """Deduplicate recent meals by identity fingerprint, preserving newest first."""
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

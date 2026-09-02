"""Non-persisted meal content fingerprinting and deduplication."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.domain.model.meal import Meal


def compute_meal_content_fingerprint(meal: Meal) -> str:
    """Generate deterministic hash of a meal's food content identity.

    Two meals are the same when they contain the same foods in the same
    amounts (AC: same foods + grams). Identity per food item is its
    canonical reference (food_reference_id / source_food_id) or normalized
    name, plus quantity and unit. Excludes dish name, macros, nutrition
    overrides, meal IDs, timestamps, images, translations, and source
    metadata.
    """
    items_data: list[dict[str, Any]] = []
    food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
    for item in food_items:
        item_name = (getattr(item, "name", "") or "").strip().lower()
        food_ref_id = getattr(item, "food_reference_id", None)
        source_food_id = getattr(item, "source_food_id", None)
        quantity = round(float(getattr(item, "quantity", 0.0) or 0.0), 3)
        unit = (getattr(item, "unit", "") or "").strip().lower()

        items_data.append(
            {
                "name": item_name,
                "food_reference_id": food_ref_id,
                "source_food_id": source_food_id,
                "quantity": quantity,
                "unit": unit,
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

    payload: dict[str, Any] = {"items": items_data}
    if not items_data:
        # Item-less meals carry no food identity; fall back to dish name so
        # distinct manual entries do not collapse into one fingerprint.
        payload["dish_name"] = (meal.dish_name or "").strip().lower()

    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def deduplicate_recent_meals(meals: list[Meal], limit: int = 10) -> list[Meal]:
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

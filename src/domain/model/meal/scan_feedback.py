"""Server-owned scan snapshots. Feedback is evidence, never automatic ground truth."""

import hashlib
import json
from typing import Any

from src.domain.model.meal import Meal

CONSENT_VERSION = "scan-learning-v1"
FEEDBACK_KINDS = {"confirmation", "identity", "portion", "nutrition", "mixed"}


def meal_snapshot(meal: Meal) -> dict[str, Any]:
    return {
        "dish_name": meal.dish_name,
        "image_id": meal.image.image_id if meal.image else None,
        "foods": [
            {
                "id": item.id,
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "confidence": item.confidence,
                "macros": item.macros.to_dict() if item.macros else None,
                "nutrition_override": item.nutrition_override.to_dict()
                if item.nutrition_override
                else None,
            }
            for item in (meal.nutrition.food_items if meal.nutrition else []) or []
        ],
        "nutrition_override": meal.nutrition.nutrition_override.to_dict()
        if meal.nutrition and meal.nutrition.nutrition_override
        else None,
        "edit_count": meal.edit_count,
    }


def original_scan_snapshot(meal: Meal) -> dict[str, Any]:
    snapshot = meal_snapshot(meal)
    try:
        raw = json.loads(meal.raw_gpt_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    provenance = raw.get("_scan_provenance") if isinstance(raw, dict) else None
    snapshot["provenance"] = provenance if isinstance(provenance, dict) else {}
    snapshot["image_id"] = meal.image.image_id if meal.image else None
    return snapshot


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

"""Copy recipe and nutrition at log time so later catalog edits cannot rewrite history."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True)
class LoggedMealSnapshot:
    catalog_meal_id: str
    catalog_meal_content_hash: str
    recipe_snapshot: dict
    nutrition_snapshot: dict


def select_logged_nutrition(
    *, verified: dict | None, estimate: dict | None
) -> dict:
    """Verified backend nutrition wins. An estimate stays marked estimated."""

    if verified is not None:
        chosen = deepcopy(verified)
        chosen["source"] = "verified_database"
        chosen["estimated"] = False
        return chosen
    if estimate is None:
        raise ValueError("logged nutrition requires verified values or an estimate")
    chosen = deepcopy(estimate)
    chosen["source"] = "ai_estimate"
    chosen["estimated"] = True
    return chosen


def build_logged_meal_snapshot(
    *,
    catalog_meal_id: str,
    content_hash: str,
    recipe_payload: dict,
    nutrition: dict,
) -> LoggedMealSnapshot:
    """Store provenance plus a detached copy of the recipe and nutrition."""

    return LoggedMealSnapshot(
        catalog_meal_id=catalog_meal_id,
        catalog_meal_content_hash=content_hash,
        recipe_snapshot=deepcopy(recipe_payload),
        nutrition_snapshot=deepcopy(nutrition),
    )

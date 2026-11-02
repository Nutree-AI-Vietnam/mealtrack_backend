"""Normalization helpers for saved suggestion compatibility snapshots."""

import uuid
from collections.abc import Iterable
from typing import Any

from src.infra.database.models.saved_suggestion import SavedSuggestionModel
from src.infra.database.models.saved_suggestion_item import SavedSuggestionItemModel
from src.infra.database.models.saved_suggestion_step import SavedSuggestionStepModel


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(data: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _nested(data: dict[str, Any], *parents: str) -> dict[str, Any]:
    for key in parents:
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _array(data: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _macro_value(data: dict[str, Any], key: str) -> float | None:
    candidates = [
        data.get(key),
        data.get(f"{key}_g"),
        _nested(data, "macros").get(key),
        _nested(data, "nutrition").get(key),
        _nested(data, "nutrition").get(f"{key}_g"),
    ]
    for value in candidates:
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def apply_normalized_saved_suggestion_fields(
    model: SavedSuggestionModel,
    suggestion_data: dict[str, Any] | None,
) -> None:
    """Populate normalized columns/children from a legacy suggestion payload."""
    data = suggestion_data if isinstance(suggestion_data, dict) else {}
    model.dish_name = _first_present(data, ("dish_name", "name", "title"))
    model.description = _first_present(data, ("description", "summary"))
    model.language = _first_present(data, ("language", "locale"))
    model.protein_g = _macro_value(data, "protein")
    model.carbs_g = _macro_value(data, "carbs")
    model.fat_g = _macro_value(data, "fat")
    model.fiber_g = _macro_value(data, "fiber")
    model.sugar_g = _macro_value(data, "sugar")
    model.items = _build_items(data)
    model.steps = _build_steps(data)


def _build_items(data: dict[str, Any]) -> list[SavedSuggestionItemModel]:
    rows: list[SavedSuggestionItemModel] = []
    for idx, item in enumerate(_array(data, "ingredients", "items", "food_items")):
        if isinstance(item, str):
            name = item.strip()
            payload: dict[str, Any] = {}
        elif isinstance(item, dict):
            payload = item
            name = str(_first_present(item, ("name", "ingredient", "food")) or "")
        else:
            continue
        if not name:
            continue
        rows.append(
            SavedSuggestionItemModel(
                id=str(uuid.uuid4()),
                name=name[:255],
                quantity=_as_float(
                    _first_present(payload, ("quantity", "amount", "serving"))
                ),
                unit=_first_present(payload, ("unit", "measure")),
                protein_g=_macro_value(payload, "protein"),
                carbs_g=_macro_value(payload, "carbs"),
                fat_g=_macro_value(payload, "fat"),
                fiber_g=_macro_value(payload, "fiber"),
                sugar_g=_macro_value(payload, "sugar"),
                position=idx,
            )
        )
    return rows


def _build_steps(data: dict[str, Any]) -> list[SavedSuggestionStepModel]:
    rows: list[SavedSuggestionStepModel] = []
    for idx, step in enumerate(
        _array(data, "instructions", "steps", "cooking_instructions")
    ):
        if isinstance(step, str):
            instruction = step.strip()
            duration = None
        elif isinstance(step, dict):
            instruction = str(
                _first_present(step, ("instruction", "text", "description")) or ""
            ).strip()
            duration = _as_float(_first_present(step, ("duration_minutes", "minutes")))
        else:
            continue
        if not instruction:
            continue
        rows.append(
            SavedSuggestionStepModel(
                id=str(uuid.uuid4()),
                instruction=instruction,
                duration_minutes=int(duration) if duration is not None else None,
                position=idx,
            )
        )
    return rows


def project_saved_suggestion_data(model: SavedSuggestionModel) -> dict[str, Any]:
    """Return normalized-first suggestion data while preserving raw snapshot fields."""
    raw = model.suggestion_data if isinstance(model.suggestion_data, dict) else {}
    projected = dict(raw)
    if model.dish_name:
        projected["dish_name"] = model.dish_name
    if model.description:
        projected["description"] = model.description
    macros = {
        key: value
        for key, value in {
            "protein": model.protein_g,
            "carbs": model.carbs_g,
            "fat": model.fat_g,
            "fiber": model.fiber_g,
            "sugar": model.sugar_g,
        }.items()
        if value is not None
    }
    if macros:
        projected["macros"] = {**projected.get("macros", {}), **macros}
    if getattr(model, "items", None):
        extras = _raw_ingredient_extras(raw, len(model.items))
        projected["ingredients"] = [
            {
                **extra,
                "name": item.name,
                "quantity": item.quantity,
                # The app's ingredient model requires a number ``amount`` and
                # a string ``unit``, as in the suggestion it saved.
                "amount": item.quantity if item.quantity is not None else 0.0,
                "unit": item.unit or "",
                "protein": item.protein_g,
                "carbs": item.carbs_g,
                "fat": item.fat_g,
                "fiber": item.fiber_g,
                "sugar": item.sugar_g,
            }
            for item, extra in zip(model.items, extras, strict=True)
        ]
    if getattr(model, "steps", None):
        projected["instructions"] = [
            {
                "instruction": step.instruction,
                "duration_minutes": step.duration_minutes,
            }
            for step in model.steps
        ]
    return projected


def _raw_ingredient_extras(raw: dict[str, Any], count: int) -> list[dict[str, Any]]:
    """Snapshot fields of each ingredient that have no normalized column.

    Kept only when the raw list lines up one-to-one with the normalized rows,
    so a field is never attached to the wrong ingredient.
    """
    items = _array(raw, "ingredients", "items", "food_items")
    named = [
        item
        for item in items
        if isinstance(item, dict)
        and str(_first_present(item, ("name", "ingredient", "food")) or "").strip()
    ]
    if len(named) != count or len(named) != len(items):
        return [{} for _ in range(count)]
    return [dict(item) for item in named]


def saved_suggestion_model_to_dict(model: SavedSuggestionModel) -> dict[str, Any]:
    """Convert ORM model to plain dict for domain/app layer consumption."""
    return {
        "id": model.id,
        "suggestion_id": model.suggestion_id,
        "meal_type": model.meal_type,
        "portion_multiplier": model.portion_multiplier,
        "suggestion_data": project_saved_suggestion_data(model),
        "saved_at": model.saved_at.isoformat() if model.saved_at else None,
    }

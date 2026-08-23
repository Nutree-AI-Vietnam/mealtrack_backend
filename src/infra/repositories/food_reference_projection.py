"""Shared food-reference projection and child-row helpers."""

from typing import Any

from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
    FoodReferenceServingProjection,
)
from src.domain.services.nutrition_integrity_policy import normalize_serving_options
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.food_reference_nutrient import FoodReferenceNutrientModel
from src.infra.database.models.food_reference_serving_size import (
    FoodReferenceServingSizeModel,
)

FOOD_REFERENCE_SEED_COLUMNS = {
    "barcode",
    "name",
    "name_vi",
    "brand",
    "category",
    "region",
    "fdc_id",
    "source_namespace",
    "source_food_id",
    "protein_100g",
    "carbs_100g",
    "fat_100g",
    "fiber_100g",
    "sugar_100g",
    "extra_nutrients",
    "serving_sizes",
    "density",
    "serving_size",
    "source",
    "is_verified",
    "image_url",
}


def food_reference_model_to_dict(model: FoodReferenceModel) -> dict[str, Any]:
    """Convert a food reference ORM row to its API/service dictionary shape."""
    return {
        "id": model.id,
        "barcode": model.barcode,
        "name": model.name,
        "name_vi": model.name_vi,
        "brand": model.brand,
        "category": model.category,
        "region": model.region,
        "fdc_id": model.fdc_id,
        "source_namespace": getattr(model, "source_namespace", None),
        "source_food_id": getattr(model, "source_food_id", None),
        "protein_100g": model.protein_100g,
        "carbs_100g": model.carbs_100g,
        "fat_100g": model.fat_100g,
        "fiber_100g": model.fiber_100g,
        "sugar_100g": model.sugar_100g,
        "serving_sizes": food_reference_serving_sizes_to_dict(model),
        "allowed_units": food_reference_allowed_units_to_dict(model),
        "density": model.density,
        "serving_size": model.serving_size,
        "extra_nutrients": food_reference_nutrients_to_dict(model),
        "source": model.source,
        "is_verified": model.is_verified,
        "integrity_status": getattr(model, "integrity_status", "unknown"),
        "integrity_policy_version": getattr(model, "integrity_policy_version", None),
        "integrity_checked_at": getattr(model, "integrity_checked_at", None),
        "integrity_reason": getattr(model, "integrity_reason", None),
        "integrity_input_digest": getattr(model, "integrity_input_digest", None),
        "integrity_review_reference": getattr(
            model, "integrity_review_reference", None
        ),
        "image_url": model.image_url,
    }


def food_reference_model_to_integrity_data(model: FoodReferenceModel) -> dict[str, Any]:
    """Return raw reference fields for a fail-closed integrity decision."""
    return {
        "protein_100g": model.protein_100g,
        "carbs_100g": model.carbs_100g,
        "fat_100g": model.fat_100g,
        "fiber_100g": model.fiber_100g,
        "sugar_100g": model.sugar_100g,
        "allowed_units": _raw_allowed_units(model),
        "source": model.source,
    }


def food_reference_model_to_nutrition_projection(
    model: FoodReferenceModel,
) -> FoodReferenceNutritionProjection:
    """Convert a food reference ORM row to the domain recipe-publication shape."""
    return FoodReferenceNutritionProjection(
        id=model.id,
        name=model.name,
        source=model.source,
        is_verified=model.is_verified,
        protein_100g=model.protein_100g,
        carbs_100g=model.carbs_100g,
        fat_100g=model.fat_100g,
        fiber_100g=model.fiber_100g or 0.0,
        sugar_100g=model.sugar_100g or 0.0,
        density_g_ml=model.density,
        name_normalized=model.name_normalized,
        source_namespace=getattr(model, "source_namespace", None),
        source_food_id=getattr(model, "source_food_id", None),
        servings=[
            FoodReferenceServingProjection(
                name=item["name"],
                grams=item.get("grams"),
                milliliters=item.get("milliliters"),
                is_default=item.get("is_default", False),
            )
            for item in _serving_sizes_for_projection(model)
        ],
    )


def build_food_reference_serving_rows(
    raw: Any,
) -> list[FoodReferenceServingSizeModel]:
    if not isinstance(raw, list):
        return []
    rows: list[FoodReferenceServingSizeModel] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("name") or item.get("label") or item.get("unit") or ""
        ).strip()
        if not name:
            continue
        description = str(
            item.get("description") or item.get("serving_description") or ""
        ).strip()
        name_vi = str(
            item.get("name_vi") or item.get("display_description") or ""
        ).strip()
        rows.append(
            FoodReferenceServingSizeModel(
                name=name[:100],
                description=description[:100] or None,
                name_vi=name_vi[:100] or None,
                grams=as_optional_float(item.get("grams") or item.get("gram_weight")),
                milliliters=as_optional_float(
                    item.get("milliliters") or item.get("ml")
                ),
                is_default=bool(item.get("is_default", idx == 0)),
                position=idx,
            )
        )
    return rows


def build_food_reference_nutrient_rows(raw: Any) -> list[FoodReferenceNutrientModel]:
    if not isinstance(raw, dict):
        return []
    rows: list[FoodReferenceNutrientModel] = []
    for key, value in sorted(raw.items()):
        if isinstance(value, dict):
            amount = as_optional_float(value.get("amount"))
            unit = value.get("unit")
        else:
            amount = as_optional_float(value)
            unit = None
        if amount is None:
            continue
        rows.append(
            FoodReferenceNutrientModel(
                nutrient_key=str(key)[:100],
                amount=amount,
                unit=str(unit)[:32] if unit else None,
            )
        )
    return rows


def food_reference_serving_sizes_to_dict(model: FoodReferenceModel) -> Any:
    rows = getattr(model, "serving_size_rows", None)
    if not rows:
        return model.serving_sizes
    return [
        {
            "name": row.name,
            "description": row.description,
            "name_vi": row.name_vi,
            "grams": row.grams,
            "milliliters": row.milliliters,
            "is_default": row.is_default,
        }
        for row in rows
    ]


def _serving_sizes_for_projection(model: FoodReferenceModel) -> list[dict[str, Any]]:
    rows = getattr(model, "serving_size_rows", None)
    if rows:
        return [
            {
                "name": row.name,
                "grams": row.grams,
                "milliliters": row.milliliters,
                "is_default": row.is_default,
            }
            for row in rows
        ]
    raw = model.serving_sizes
    if not isinstance(raw, list):
        return []
    servings: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("name") or item.get("label") or item.get("unit") or ""
        ).strip()
        if not name:
            continue
        servings.append(
            {
                "name": name,
                "grams": as_optional_float(
                    item.get("grams") or item.get("gram_weight")
                ),
                "milliliters": as_optional_float(
                    item.get("milliliters") or item.get("ml")
                ),
                "is_default": bool(item.get("is_default", idx == 0)),
            }
        )
    return servings


def food_reference_allowed_units_to_dict(
    model: FoodReferenceModel,
) -> list[dict[str, Any]]:
    raw_rows = getattr(model, "serving_size_rows", None)
    if raw_rows:
        units = [
            {
                "unit": row.name,
                "gram_weight": row.grams,
                "description": row.description or row.name,
                **(
                    {"display_description": row.name_vi}
                    if row.name_vi
                    else {}
                ),
            }
            for row in raw_rows
            if row.grams is not None and row.grams > 0
        ]
    else:
        units = _legacy_serving_sizes_to_allowed_units(model.serving_sizes)
    return normalize_serving_options(units) or []


def _legacy_serving_sizes_to_allowed_units(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    units: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(
            item.get("name") or item.get("label") or item.get("unit") or ""
        ).strip()
        grams = as_optional_float(item.get("grams") or item.get("gram_weight"))
        if name and grams is not None and grams > 0:
            units.append(
                {
                    "unit": name,
                    "gram_weight": grams,
                    "description": item.get("description") or name,
                }
            )
    return units


def _raw_allowed_units(model: FoodReferenceModel) -> list[dict[str, Any]]:
    rows = getattr(model, "serving_size_rows", None)
    if rows:
        return [
            {
                "unit": row.name,
                "gram_weight": row.grams,
                "description": row.description or row.name,
            }
            for row in rows
        ]
    return _legacy_serving_sizes_to_allowed_units(model.serving_sizes)


def food_reference_nutrients_to_dict(model: FoodReferenceModel) -> Any:
    rows = getattr(model, "nutrient_rows", None)
    if not rows:
        return model.extra_nutrients
    raw = model.extra_nutrients if isinstance(model.extra_nutrients, dict) else {}
    projected = dict(raw)
    for row in rows:
        legacy_value = raw.get(row.nutrient_key)
        if isinstance(legacy_value, dict):
            projected[row.nutrient_key] = {
                **legacy_value,
                "amount": row.amount,
                "unit": row.unit,
            }
        elif row.nutrient_key in raw:
            projected[row.nutrient_key] = row.amount
        else:
            projected[row.nutrient_key] = {
                "amount": row.amount,
                "unit": row.unit,
            }
    return projected


def as_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

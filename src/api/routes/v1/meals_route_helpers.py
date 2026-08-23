"""Shared helpers for meals route modules."""

from datetime import datetime
from typing import Any

from src.api.exceptions import ValidationException
from src.api.schemas.response.meal_responses import ParsedFoodItem
from src.domain.model.nutrition.macros import Macros as MacrosModel


async def load_food_reference_display_projections(
    meal,
    food_reference_repository,
    *,
    language: str | None = None,
) -> dict[int, dict[str, Any]]:
    """Load id-keyed catalog display names for every tracked line on a meal.

    Unlike nutrition-density batching, this must include items that already
    carry a ``source_snapshot`` — display names always follow the live
    catalog row, while kcal/macros stay frozen on the snapshot.
    """
    food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
    food_reference_ids = {
        item.food_reference_id
        for item in food_items
        if getattr(item, "food_reference_id", None) is not None
    }
    if not food_reference_ids:
        return {}
    projections = await food_reference_repository.get_display_projections(
        list(food_reference_ids),
        language=language,
    )
    if not language or language == "en":
        return projections
    return await _enrich_serving_labels(meal, projections, language)


async def _enrich_serving_labels(meal, projections, language: str):
    from src.api.base_dependencies import get_serving_label_dependencies

    enrich_meal_serving_labels, translation_service, uow_factory = (
        get_serving_label_dependencies()
    )

    return await enrich_meal_serving_labels(
        meal,
        projections,
        language=language,
        translation_service=translation_service,
        uow_factory=uow_factory,
    )


def parse_target_date(target_date: str | None):
    if not target_date:
        return None
    try:
        return datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValidationException(
            message="Invalid date format. Use YYYY-MM-DD format.",
            error_code="INVALID_DATE_FORMAT",
            details={"date": target_date},
        ) from e


def parsed_food_item_to_response(item) -> ParsedFoodItem:
    return ParsedFoodItem(
        name=item.name,
        quantity=item.quantity,
        unit=item.unit,
        calories=MacrosModel(
            protein=item.protein,
            carbs=item.carbs,
            fat=item.fat,
            fiber=item.fiber if hasattr(item, "fiber") and item.fiber else 0.0,
        ).total_calories,
        protein=item.protein,
        carbs=item.carbs,
        fat=item.fat,
        fiber=getattr(item, "fiber", 0.0) or 0.0,
        sugar=getattr(item, "sugar", 0.0) or 0.0,
        data_source=item.data_source,
        fdc_id=item.fdc_id,
        food_id=getattr(item, "food_id", None),
        food_reference_id=getattr(item, "food_reference_id", None),
        origin=getattr(item, "origin", None),
        source_namespace=getattr(item, "source_namespace", None),
        source_food_id=getattr(item, "source_food_id", None),
        nutrition_basis=getattr(item, "nutrition_basis", None),
        nutrition_contract_version=getattr(item, "nutrition_contract_version", None),
        calories_per_100g=getattr(item, "calories_per_100g", None),
        protein_per_100g=getattr(item, "protein_per_100g", None),
        carbs_per_100g=getattr(item, "carbs_per_100g", None),
        fat_per_100g=getattr(item, "fat_per_100g", None),
        fiber_per_100g=getattr(item, "fiber_per_100g", None),
        sugar_per_100g=getattr(item, "sugar_per_100g", None),
        canonical_name=getattr(item, "canonical_name", None),
        source_snapshot=getattr(item, "source_snapshot", None),
        serving_options=getattr(item, "serving_options", None) or [],
    )

"""Materialize a catalog recommendation slot as a normal meal."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from uuid import uuid4

from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationNotFoundError,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.meal_recommendation import (
    CatalogMeal,
    CatalogMealIngredient,
    PersistedMealRecommendationPlan,
    PersistedMealRecommendationSlot,
)
from src.domain.model.nutrition import FoodItem, Macros, Nutrition
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
)
from src.domain.services.meal_recommendation.ingredient_quantity_conversion_service import (
    IngredientQuantityConversionError,
    IngredientQuantityConversionService,
)
from src.domain.services.weekly_meal_planner.meal_log_snapshot import (
    build_logged_meal_snapshot,
    select_logged_nutrition,
)
from src.domain.utils.timezone_utils import noon_utc_for_date

_CATALOG_CONVERTER = IngredientQuantityConversionService(
    allow_unverified=True,
    allow_unapproved_sources=True,
    allow_implausible_macros=True,
    allow_common_unit_fallbacks=True,
)
_WEIGHT_UNITS_TO_GRAMS = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "oz": 28.35,
    "lb": 453.6,
    "ml": 1.0,
    "l": 1000.0,
}


class RecommendedMealMaterializationService:
    """Build and persist normal meals from immutable catalog recipe snapshots."""

    async def materialize(
        self,
        uow,
        *,
        plan: PersistedMealRecommendationPlan,
        slot: PersistedMealRecommendationSlot,
    ) -> Meal:
        if slot.selected is None or slot.selected.catalog_meal is None:
            raise MealRecommendationNotFoundError
        catalog_meal = slot.selected.catalog_meal
        return await self.materialize_from_catalog(
            uow,
            user_id=plan.user_id,
            catalog_meal=catalog_meal,
            meal_date=slot.slot_date,
            meal_type=slot.meal_type,
            timezone=plan.timezone,
        )

    async def materialize_from_catalog(
        self,
        uow,
        *,
        user_id: str,
        catalog_meal: CatalogMeal,
        meal_date: date,
        meal_type: str,
        timezone: str,
        portion_multiplier: float = 1.0,
        source: str = "meal_recommendation",
    ) -> Meal:
        if portion_multiplier not in {0.5, 1.0, 1.5, 2.0}:
            raise ValueError("portion_multiplier must be one of 0.5, 1, 1.5, or 2")
        projections = await _load_nutrition_projections(uow, catalog_meal)
        food_items = [
            _food_item_for_ingredient(
                ingredient,
                projections.get(ingredient.food_reference_id),
                portion_multiplier,
            )
            for ingredient in catalog_meal.ingredients
        ]
        if _needs_catalog_macro_fallback(food_items):
            food_items = _distribute_catalog_macros(
                food_items, catalog_meal, portion_multiplier
            )
        meal_time = noon_utc_for_date(meal_date, timezone)
        verified_nutrition = {
            "calories": int(round(catalog_meal.calories * portion_multiplier)),
            "protein_g": float(catalog_meal.protein_g) * portion_multiplier,
            "carbs_g": float(catalog_meal.carbs_g) * portion_multiplier,
            "fat_g": float(catalog_meal.fat_g) * portion_multiplier,
            "fiber_g": float(catalog_meal.fiber_g) * portion_multiplier,
        }
        logged = build_logged_meal_snapshot(
            catalog_meal_id=catalog_meal.id,
            content_hash=catalog_meal.content_hash,
            recipe_payload=_recipe_payload(catalog_meal, portion_multiplier),
            nutrition=select_logged_nutrition(
                verified=verified_nutrition,
                estimate=getattr(catalog_meal, "ai_nutrition_estimate", None),
            ),
        )
        meal = Meal(
            meal_id=str(uuid4()),
            user_id=user_id,
            status=MealStatus.READY,
            created_at=meal_time,
            ready_at=meal_time,
            image=_meal_image_for_catalog(catalog_meal),
            dish_name=catalog_meal.name,
            nutrition=Nutrition(
                macros=Macros(
                    protein=float(catalog_meal.protein_g) * portion_multiplier,
                    carbs=float(catalog_meal.carbs_g) * portion_multiplier,
                    fat=float(catalog_meal.fat_g) * portion_multiplier,
                    fiber=float(catalog_meal.fiber_g) * portion_multiplier,
                ),
                food_items=food_items,
            ),
            meal_type=meal_type,
            source=source,
            catalog_meal_id=logged.catalog_meal_id,
            catalog_meal_content_hash=logged.catalog_meal_content_hash,
            recipe_snapshot=logged.recipe_snapshot,
            nutrition_snapshot=logged.nutrition_snapshot,
        )
        return await uow.meals.save(meal)


def _recipe_payload(catalog_meal: CatalogMeal, portion_multiplier: float = 1.0) -> dict:
    payload = getattr(catalog_meal, "recipe_payload", None)
    if payload:
        scaled = deepcopy(payload)
        for ingredient in scaled.get("ingredients") or []:
            quantity = ingredient.get("quantity")
            if isinstance(quantity, (int, float)) and not isinstance(quantity, bool):
                ingredient["quantity"] = quantity * portion_multiplier
    else:
        scaled = {
            "recipe_name": catalog_meal.name,
            "ingredients": [
                {
                    "name": ingredient.name,
                    "quantity": float(ingredient.quantity) * portion_multiplier,
                    "unit": ingredient.unit,
                }
                for ingredient in catalog_meal.ingredients
            ],
        }
    scaled["portion_multiplier"] = portion_multiplier
    return scaled


def _meal_image_for_catalog(catalog_meal: CatalogMeal) -> MealImage:
    """Build a meal image row from catalog URL, or a placeholder when absent."""

    url = (catalog_meal.image_url or "").strip() or None
    return MealImage(
        image_id=str(uuid4()),
        format="jpeg",
        size_bytes=1,
        url=url,
    )


async def _load_nutrition_projections(
    uow,
    catalog_meal: CatalogMeal,
) -> dict[int, FoodReferenceNutritionProjection]:
    food_reference_ids = [
        ingredient.food_reference_id for ingredient in catalog_meal.ingredients
    ]
    if not food_reference_ids:
        return {}
    repo = getattr(uow, "food_references", None)
    loader = getattr(repo, "get_nutrition_projections", None)
    if loader is None:
        return {}
    return await loader(food_reference_ids)


def _food_item_for_ingredient(
    ingredient: CatalogMealIngredient,
    projection: FoodReferenceNutritionProjection | None,
    portion_multiplier: float = 1.0,
) -> FoodItem:
    return FoodItem(
        id=str(uuid4()),
        name=ingredient.name,
        quantity=float(ingredient.quantity) * portion_multiplier,
        unit=ingredient.unit,
        macros=_scale_macros(
            _macros_from_projection(ingredient, projection), portion_multiplier
        ),
        food_reference_id=ingredient.food_reference_id,
    )


def _scale_macros(macros: Macros, multiplier: float) -> Macros:
    return Macros(
        protein=macros.protein * multiplier,
        carbs=macros.carbs * multiplier,
        fat=macros.fat * multiplier,
        fiber=macros.fiber * multiplier,
        sugar=macros.sugar * multiplier,
    )


def _macros_from_projection(
    ingredient: CatalogMealIngredient,
    projection: FoodReferenceNutritionProjection | None,
) -> Macros:
    if projection is None:
        return Macros(protein=0, carbs=0, fat=0, fiber=0, sugar=0)
    try:
        resolved = _CATALOG_CONVERTER.resolve(
            reference=projection,
            quantity=float(ingredient.quantity),
            unit=ingredient.unit,
            display_name=ingredient.name,
        )
    except IngredientQuantityConversionError:
        return Macros(protein=0, carbs=0, fat=0, fiber=0, sugar=0)
    return Macros(
        protein=resolved.protein,
        carbs=resolved.carbs,
        fat=resolved.fat,
        fiber=resolved.fiber,
        sugar=resolved.sugar,
    )


def _needs_catalog_macro_fallback(food_items: list[FoodItem]) -> bool:
    if not food_items:
        return False
    return all(
        item.macros.protein == 0 and item.macros.carbs == 0 and item.macros.fat == 0
        for item in food_items
    )


def _distribute_catalog_macros(
    food_items: list[FoodItem],
    catalog_meal: CatalogMeal,
    portion_multiplier: float = 1.0,
) -> list[FoodItem]:
    total_weight = sum(_estimated_grams(item) for item in food_items)
    if total_weight <= 0:
        return food_items
    protein = float(catalog_meal.protein_g) * portion_multiplier
    carbs = float(catalog_meal.carbs_g) * portion_multiplier
    fat = float(catalog_meal.fat_g) * portion_multiplier
    fiber = float(catalog_meal.fiber_g) * portion_multiplier
    sugar = float(catalog_meal.sugar_g) * portion_multiplier
    distributed: list[FoodItem] = []
    for item in food_items:
        ratio = _estimated_grams(item) / total_weight
        distributed.append(
            FoodItem(
                id=item.id,
                name=item.name,
                quantity=item.quantity,
                unit=item.unit,
                macros=Macros(
                    protein=round(protein * ratio, 1),
                    carbs=round(carbs * ratio, 1),
                    fat=round(fat * ratio, 1),
                    fiber=round(fiber * ratio, 1),
                    sugar=round(sugar * ratio, 1),
                ),
                food_reference_id=item.food_reference_id,
            )
        )
    return distributed


def _estimated_grams(item: FoodItem) -> float:
    unit = (item.unit or "").lower().strip()
    return item.quantity * _WEIGHT_UNITS_TO_GRAMS.get(unit, 1.0)

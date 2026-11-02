"""Materialize a catalog recommendation slot as a normal meal."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any
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
from src.domain.model.nutrition.extra_nutrients import (
    complete_micros_from_per_100g_portions,
)
from src.domain.model.nutrition.micros_ops import scale_micros
from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot
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
from src.domain.utils.timezone_utils import is_same_day_log, log_moment, utc_now

_CATALOG_CONVERTER = IngredientQuantityConversionService(
    allow_unverified=True,
    allow_unapproved_sources=True,
    allow_implausible_macros=True,
    allow_common_unit_fallbacks=True,
)
# Nutrition holds at most this many food items.
_MAX_FOOD_ITEMS = 50
_MAX_FOOD_ITEM_QUANTITY = 10_000.0
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
        recipe_micros = complete_micros_from_per_100g_portions(
            _ingredient_micro_portions(catalog_meal, projections)
        )
        recipe_micros = scale_micros(recipe_micros, portion_multiplier)
        now = utc_now()
        meal_time = log_moment(meal_date, timezone, now)
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
                micros=recipe_micros,
                food_items=food_items,
            ),
            meal_type=meal_type,
            source=source,
            catalog_meal_id=logged.catalog_meal_id,
            catalog_meal_content_hash=logged.catalog_meal_content_hash,
            recipe_snapshot=logged.recipe_snapshot,
            nutrition_snapshot=logged.nutrition_snapshot,
            logged_on_day=is_same_day_log(meal_date, timezone, now),
        )
        return await uow.meals.save(meal)

    async def materialize_from_saved_recipe(
        self,
        uow,
        *,
        user_id: str,
        recipe: SavedRecipeSnapshot,
        meal_date: date,
        meal_type: str,
        timezone: str,
        portion_multiplier: float = 1.0,
        source: str = "weekly_meal_planner",
    ) -> Meal:
        """Persist a normal meal from a plan slot's copy of a saved recipe.

        Macros are the saved per-serving macros times the portion; calories
        follow from them with the backend formula. Ingredients are the
        recipe's lines divided by its servings, times the portion.
        """
        if portion_multiplier not in {0.5, 1.0, 1.5, 2.0}:
            raise ValueError("portion_multiplier must be one of 0.5, 1, 1.5, or 2")
        macros = recipe.macros(portion_multiplier)
        food_items = await _saved_recipe_food_items(uow, recipe, portion_multiplier)
        now = utc_now()
        meal_time = log_moment(meal_date, timezone, now)
        meal = Meal(
            meal_id=str(uuid4()),
            user_id=user_id,
            status=MealStatus.READY,
            created_at=meal_time,
            ready_at=meal_time,
            image=MealImage(
                image_id=str(uuid4()), format="jpeg", size_bytes=1, url=recipe.image_url
            ),
            dish_name=recipe.name,
            nutrition=Nutrition(macros=macros, food_items=food_items or None),
            meal_type=meal_type,
            source=source,
            recipe_snapshot={
                "recipe_name": recipe.name,
                "saved_suggestion_id": recipe.suggestion_id,
                "servings": recipe.servings,
                "ingredients": [item.to_dict() for item in recipe.ingredients],
                "portion_multiplier": portion_multiplier,
            },
            nutrition_snapshot={
                "calories": macros.total_calories,
                "protein_g": macros.protein,
                "carbs_g": macros.carbs,
                "fat_g": macros.fat,
                "fiber_g": macros.fiber,
                "source": "saved_recipe",
            },
            description=recipe.description,
            instructions=[step.to_dict() for step in recipe.steps] or None,
            cook_time_min=recipe.cook_time_minutes,
            cuisine_type=recipe.cuisine_type,
            origin_country=recipe.origin_country,
            emoji=recipe.emoji,
            logged_on_day=is_same_day_log(meal_date, timezone, now),
        )
        return await uow.meals.save(meal)


async def _saved_recipe_food_items(
    uow, recipe: SavedRecipeSnapshot, portion_multiplier: float
) -> list[FoodItem]:
    """One food item per usable line, scaled to the eaten portion.

    Line macros cover the whole recipe and are divided like the amounts. When
    no line has macros, the meal's macros are spread by weight. A food
    reference id is kept only while that reference still exists.
    """
    factor = portion_multiplier / recipe.servings
    known_ids = set(
        await _load_nutrition_projections_by_id(
            uow,
            [
                item.food_reference_id
                for item in recipe.ingredients
                if item.food_reference_id is not None
            ],
        )
    )
    items: list[FoodItem] = []
    for line in recipe.ingredients:
        if len(items) == _MAX_FOOD_ITEMS:
            break
        if line.amount is not None:
            quantity, unit = line.amount * factor, line.unit or "piece"
        elif line.grams is not None:
            quantity, unit = line.grams * factor, "g"
        else:
            continue
        if not 0 < quantity <= _MAX_FOOD_ITEM_QUANTITY:
            continue
        items.append(
            FoodItem(
                id=str(uuid4()),
                name=line.name,
                quantity=quantity,
                unit=unit,
                macros=Macros(
                    protein=(line.protein or 0.0) * factor,
                    carbs=(line.carbs or 0.0) * factor,
                    fat=(line.fat or 0.0) * factor,
                    fiber=(line.fiber or 0.0) * factor,
                    sugar=(line.sugar or 0.0) * factor,
                ),
                food_reference_id=(
                    line.food_reference_id
                    if line.food_reference_id in known_ids
                    else None
                ),
                is_custom=line.food_reference_id not in known_ids,
            )
        )
    if _needs_catalog_macro_fallback(items):
        items = _distribute_macros(items, recipe.macros(portion_multiplier))
    return items


async def _load_nutrition_projections_by_id(
    uow, food_reference_ids: list[int]
) -> dict[int, FoodReferenceNutritionProjection]:
    if not food_reference_ids:
        return {}
    repo = getattr(uow, "food_references", None)
    loader = getattr(repo, "get_nutrition_projections", None)
    if loader is None:
        return {}
    return await loader(food_reference_ids)


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
        ingredient.food_reference_id
        for ingredient in catalog_meal.ingredients
        if ingredient.food_reference_id is not None
    ]
    if not food_reference_ids:
        return {}
    repo = getattr(uow, "food_references", None)
    loader = getattr(repo, "get_nutrition_projections", None)
    if loader is None:
        return {}
    return await loader(food_reference_ids, preserve_nutrient_units=True)


def _ingredient_micro_portions(
    catalog_meal: CatalogMeal,
    projections: dict[int, FoodReferenceNutritionProjection],
) -> list[tuple[dict[str, Any] | None, float]]:
    portions: list[tuple[dict[str, Any] | None, float]] = []
    for ingredient in catalog_meal.ingredients:
        quantity = float(ingredient.quantity)
        if quantity <= 0:
            continue
        projection = projections.get(ingredient.food_reference_id)
        if projection is None:
            portions.append((None, 0.0))
            continue
        try:
            resolved = _CATALOG_CONVERTER.resolve(
                reference=projection,
                quantity=quantity,
                unit=ingredient.unit,
                display_name=ingredient.name,
            )
        except IngredientQuantityConversionError:
            portions.append((None, 0.0))
            continue
        portions.append((projection.extra_nutrients, resolved.grams))
    return portions


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
    return _distribute_macros(
        food_items,
        Macros(
            protein=float(catalog_meal.protein_g) * portion_multiplier,
            carbs=float(catalog_meal.carbs_g) * portion_multiplier,
            fat=float(catalog_meal.fat_g) * portion_multiplier,
            fiber=float(catalog_meal.fiber_g) * portion_multiplier,
            sugar=float(catalog_meal.sugar_g) * portion_multiplier,
        ),
    )


def _distribute_macros(food_items: list[FoodItem], totals: Macros) -> list[FoodItem]:
    """Spread meal-level macros over the items by estimated weight."""
    total_weight = sum(_estimated_grams(item) for item in food_items)
    if total_weight <= 0:
        return food_items
    protein, carbs, fat = totals.protein, totals.carbs, totals.fat
    fiber, sugar = totals.fiber, totals.sugar
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
                is_custom=item.is_custom,
            )
        )
    return distributed


def _estimated_grams(item: FoodItem) -> float:
    unit = (item.unit or "").lower().strip()
    return item.quantity * _WEIGHT_UNITS_TO_GRAMS.get(unit, 1.0)

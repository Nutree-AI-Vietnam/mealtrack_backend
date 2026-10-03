"""Build typed, casefold-exact projections from canonical recipe conversion."""

import hashlib
import json
from dataclasses import replace

from src.domain.services.weekly_meal_planner.allergen_constraint import (
    normalize_allergen_code,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    _MEAT_WORDS,
    _PORK_WORDS,
    _contains_any,
    _haystack,
    is_non_meal_title,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionAllergenORM,
)
from src.infra.repositories.food_reference_projection import (
    food_reference_model_to_nutrition_projection,
)

PROJECTION_SCHEMA_VERSION = 1
CONVERSION_CONTRACT = "canonical-catalog-conversion-v1"
BROWSE_MEAT_WORDS = (
    "beef",
    "chicken",
    "pork",
    "fish",
    "shrimp",
    "meat",
    "thịt",
    "gà",
    "bò",
    "cá",
)
BROWSE_PORK_WORDS = ("pork", "ham", "bacon", "sausage", "thịt heo", "thịt lợn")


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def projection_values(row, meal) -> tuple[dict, list[MealCatalogProjectionAllergenORM]]:
    """Do not use timestamps: micronutrient edits leave selection inputs stable."""
    names = [ingredient.name for ingredient in meal.ingredients]
    browse_text = " ".join([meal.name, meal.description or "", *names]).casefold()
    search_text = " ".join(
        [meal.name, meal.cuisine, meal.description or "", *names]
    ).casefold()
    selection_text = _haystack(meal)
    ingredients = [
        {
            "position": ingredient.position,
            "food_reference_id": ingredient.food_reference_id,
            "display_name": ingredient.display_name,
            "quantity": ingredient.quantity,
            "unit": ingredient.unit,
            "category": ingredient.category,
            "quantity_text": ingredient.quantity_text,
        }
        for ingredient in row.ingredients
    ]
    food_inputs = []
    enrichment_inputs = []
    for ingredient in row.ingredients:
        food = ingredient.food_reference
        if food is None:
            continue
        projection = food_reference_model_to_nutrition_projection(food)
        food_inputs.append(
            {
                "id": projection.id,
                "name": projection.name,
                "source": projection.source,
                "is_verified": projection.is_verified,
                "protein": projection.protein_100g,
                "carbs": projection.carbs_100g,
                "fat": projection.fat_100g,
                "fiber": projection.fiber_100g,
                "sugar": projection.sugar_100g,
                "density": projection.density_g_ml,
                "servings": [vars(serving) for serving in projection.servings],
            }
        )
        enrichment_inputs.append(
            {
                "id": projection.id,
                "fdc_id": projection.fdc_id,
                "nutrients": projection.extra_nutrients,
            }
        )
    ingredient_digest = _digest([CONVERSION_CONTRACT, ingredients, food_inputs])
    summary = replace(
        meal,
        ingredients=(),
        steps=(),
        recipe_payload=None,
        nutrition_micros=None,
        nutrition_micros_sources={},
        selection_features=None,
    )
    payload = summary.to_dict()
    selection_digest = _digest(
        [PROJECTION_SCHEMA_VERSION, ingredient_digest, payload, meal.allergen_codes]
    )
    translation_digest = _digest(
        [
            meal.name,
            meal.cuisine,
            meal.description,
            meal.summary,
            meal.equipment,
            meal.tag,
            meal.allergens,
            names,
            [(step.step_number, step.title, step.description) for step in row.steps],
            row.recipe_payload.get("instructions") if row.recipe_payload else None,
        ]
    )
    values = {
        "catalog_meal_id": meal.id,
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "selection_digest": selection_digest,
        "ingredient_digest": ingredient_digest,
        "translation_digest": translation_digest,
        "enrichment_digest": _digest(
            [
                ingredient_digest,
                meal.name,
                meal.cuisine,
                meal.base_servings or 1,
                enrichment_inputs,
                row.recipe_payload.get("nutrition") if row.recipe_payload else None,
            ]
        ),
        "query_dirty": False,
        "nutrition_dirty": False,
        "translation_dirty": False,
        "enrichment_dirty": False,
        "name_casefold": meal.name.casefold(),
        "cuisine_casefold": meal.cuisine.casefold(),
        "search_text": search_text,
        "browse_constraint_text": browse_text,
        "selection_constraint_text": selection_text,
        "browse_vegetarian": not any(term in browse_text for term in BROWSE_MEAT_WORDS),
        "browse_no_pork": not any(term in browse_text for term in BROWSE_PORK_WORDS),
        "selection_contains_meat": _contains_any(selection_text, _MEAT_WORDS),
        "selection_contains_pork": _contains_any(selection_text, _PORK_WORDS),
        "non_meal": is_non_meal_title(meal.name, meal.tag),
        "total_minutes": int(meal.prep_time_minutes or 0)
        + int(meal.cook_time_minutes or 0),
        "popularity_rank": meal.popularity_rank,
        "publication_status": meal.publication_status,
        "nutrition_status": meal.nutrition_status,
        "protein_g": meal.protein_g,
        "carbs_g": meal.carbs_g,
        "fat_g": meal.fat_g,
        "fiber_g": meal.fiber_g,
        "sugar_g": meal.sugar_g,
        "calories": meal.calories,
        "summary_payload": payload,
    }
    values.update(
        {
            f"{kind}_eligible": kind in meal.meal_types
            for kind in ("breakfast", "lunch", "dinner", "snack")
        }
    )
    links = [
        MealCatalogProjectionAllergenORM(
            catalog_meal_id=meal.id,
            allergen_id=link.allergen_id,
            code_normalized=normalize_allergen_code(link.allergen.code),
        )
        for link in row.allergen_links
    ]
    return values, links

"""Assemble the reviewable draft and its saved-recipe snapshot."""

from __future__ import annotations

from typing import Any

from src.app.services.recipe_import.recipe_nutrition_pricer import PricedRecipe
from src.domain.model.recipe_import.enums import (
    RecipeImportSourceType,
    RecipeImportWarning,
)
from src.domain.model.recipe_import.extracted_recipe import ExtractedRecipe
from src.domain.model.recipe_import.imported_recipe import (
    ImportedRecipe,
    RecipeMacros,
    RecipeSourceInfo,
)
from src.domain.model.recipe_import.limits import MIN_QUANTIFIED_SHARE_FOR_CAPTION
from src.domain.model.recipe_import.source_material import RecipeSourceMaterial

SAVED_SUGGESTION_ID_PREFIX = "recipe_import_"


def build_imported_recipe(
    *,
    import_id: str,
    extracted: ExtractedRecipe,
    priced: PricedRecipe,
    material: RecipeSourceMaterial,
    source_type: RecipeImportSourceType,
    warnings: tuple[RecipeImportWarning, ...] = (),
) -> ImportedRecipe:
    structured = material.structured_recipe
    found = list(warnings)
    if priced.servings_estimated or not (
        extracted.servings_stated or (structured and structured.servings)
    ):
        found.append(RecipeImportWarning.SERVINGS_ESTIMATED)
    if (
        not extracted.quantities_stated
        or extracted.quantified_share < MIN_QUANTIFIED_SHARE_FOR_CAPTION
        or any(
            item.amount_estimated for item in extracted.ingredients if not item.optional
        )
    ):
        found.append(RecipeImportWarning.QUANTITIES_ESTIMATED)
    if priced.nutrition.unpriced_ingredients:
        found.append(RecipeImportWarning.NUTRITION_PARTIAL)
    if priced.nutrition.estimated:
        found.append(RecipeImportWarning.NUTRITION_ESTIMATED)
    return ImportedRecipe(
        import_id=import_id,
        language=extracted.language,
        name=extracted.name,
        servings=priced.nutrition.servings,
        ingredients=priced.ingredients,
        steps=extracted.steps,
        nutrition=priced.nutrition,
        source=RecipeSourceInfo(
            type=source_type,
            platform=material.platform,
            url=material.canonical_url or material.source_url,
            title=material.title or (structured.name if structured else None),
            author=material.author or (structured.author if structured else None),
            site_name=material.site_name,
            signals=material.signals(),
        ),
        english_name=extracted.english_name,
        description=extracted.description
        or (structured.description if structured else None),
        emoji=extracted.emoji,
        cuisine_type=extracted.cuisine_type
        or (structured.cuisine if structured else None),
        origin_country=extracted.origin_country,
        meal_type=extracted.meal_type,
        prep_time_minutes=extracted.prep_time_minutes
        or (structured.prep_time_minutes if structured else None),
        cook_time_minutes=extracted.cook_time_minutes
        or (structured.cook_time_minutes if structured else None),
        total_time_minutes=extracted.total_time_minutes
        or (structured.total_time_minutes if structured else None),
        image_url=material.image_url or (structured.image_url if structured else None),
        warnings=tuple(dict.fromkeys(found)),
    )


def total_minutes(recipe: ImportedRecipe) -> int:
    if recipe.total_time_minutes:
        return recipe.total_time_minutes
    parts = (recipe.prep_time_minutes or 0) + (recipe.cook_time_minutes or 0)
    if parts:
        return parts
    return sum(step.duration_minutes or 0 for step in recipe.steps)


def saved_suggestion_id(import_id: str) -> str:
    return f"{SAVED_SUGGESTION_ID_PREFIX}{import_id}"


def saved_suggestion_payload(recipe: ImportedRecipe) -> dict[str, Any]:
    """Snapshot in the saved-suggestion shape the app already renders.

    ``macros`` is one serving; ingredient amounts are for the whole recipe,
    which ``servings`` makes explicit.
    """
    return {
        "id": saved_suggestion_id(recipe.import_id),
        "meal_name": recipe.name,
        "english_name": recipe.english_name,
        "emoji": recipe.emoji,
        "description": recipe.description or "",
        "macros": _macros(recipe.nutrition.per_serving, integer_calories=True),
        "ingredients": [
            {
                "name": item.name,
                "amount": item.quantity if item.quantity is not None else 0.0,
                "unit": item.unit or "",
                "grams": item.grams,
                "amount_estimated": item.amount_estimated,
                "eaten_share": item.eaten_share,
                "english_name": item.english_name,
                "note": item.note,
                "optional": item.optional,
                "group": item.group,
                "food_reference_id": item.food_reference_id,
                "calories": item.macros.calories if item.macros else None,
                "protein": item.macros.protein if item.macros else None,
                "carbs": item.macros.carbs if item.macros else None,
                "fat": item.macros.fat if item.macros else None,
                "fiber": item.macros.fiber if item.macros else None,
                "sugar": item.macros.sugar if item.macros else None,
            }
            for item in recipe.ingredients
        ],
        "recipe_steps": [
            {
                "step": index,
                "instruction": step.instruction,
                "duration_minutes": step.duration_minutes,
            }
            for index, step in enumerate(recipe.steps, start=1)
        ],
        "prep_time_minutes": total_minutes(recipe),
        "confidence_score": 0.9,
        "origin_country": recipe.origin_country,
        "cuisine_type": recipe.cuisine_type,
        "language": recipe.language,
        "image_url": recipe.image_url,
        "servings": recipe.servings,
        "total_macros": _macros(recipe.nutrition.total, integer_calories=False),
        "recipe_import": {
            "import_id": recipe.import_id,
            "source_type": recipe.source.type.value,
            "platform": recipe.source.platform.value,
            "source_url": recipe.source.url,
            "source_title": recipe.source.title,
            "source_author": recipe.source.author,
            "prep_time_minutes": recipe.prep_time_minutes,
            "cook_time_minutes": recipe.cook_time_minutes,
            "total_time_minutes": recipe.total_time_minutes,
            "warnings": [warning.value for warning in recipe.warnings],
        },
    }


def _macros(macros: RecipeMacros, *, integer_calories: bool) -> dict[str, Any]:
    return {
        "calories": int(round(macros.calories))
        if integer_calories
        else macros.calories,
        "protein": macros.protein,
        "carbs": macros.carbs,
        "fat": macros.fat,
        "fiber": macros.fiber,
        "sugar": macros.sugar,
    }

"""
Single-recipe generation attempt with macro validation and MealSuggestion assembly.
Used by ParallelRecipeGenerator for each individual recipe AI call.
"""

import asyncio
import logging
import uuid

from src.domain.model.meal_suggestion import (
    Ingredient,
    MacroEstimate,
    MealSuggestion,
    MealType,
    RecipeStep,
    SuggestionSession,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.emoji_validator import validate_emoji
from src.domain.services.meal_suggestion.macro_validation_service import (
    MacroValidationService,
)
from src.domain.services.meal_suggestion.nutrition_lookup_service import (
    NutritionLookupService,
)

logger = logging.getLogger(__name__)

PARALLEL_SINGLE_MEAL_TOKENS = 1200  # was 4000; worst-case output ~600 tokens, 2x safety margin
PARALLEL_SINGLE_MEAL_TIMEOUT = 20  # was 35
# User-tapped /recipes: one meal, worth waiting. Discovery stays on the short timeout.
SELECTED_RECIPE_ATTEMPT_TIMEOUT = 40


async def attempt_recipe_generation(
    generation_service: MealGenerationServicePort,
    macro_validator: MacroValidationService,
    nutrition_lookup: NutritionLookupService,
    prompt: str,
    meal_name: str,
    index: int,
    model_purpose: str,
    recipe_system: str,
    session: SuggestionSession,
    is_retry: bool = False,
    reject_on_scale_out_of_range: bool = True,
    fill_missing_steps: bool = False,
    recipe_schema: type | None = None,
    timeout: float | None = None,
) -> MealSuggestion | None:
    """
    Single AI call to generate one recipe. Returns MealSuggestion on success, None on failure.

    Macros are calculated deterministically from ingredients via NutritionLookupService.
    AI-reported macro values are ignored.

    Args:
        generation_service: Port for calling the AI generation API
        macro_validator: Validates and corrects raw macro values (used for discovery path)
        nutrition_lookup: Deterministic macro calculator (T1→T2→T3 tier lookup)
        prompt: Recipe-specific prompt text
        meal_name: Name of the meal being generated
        index: Slot index (0-3) for logging
        model_purpose: Model pool key ("recipe_primary" / "recipe_secondary")
        recipe_system: System prompt with JSON schema instructions
        session: Current suggestion session (used for fallback values)
        is_retry: Whether this is a retry attempt on an alternate model
    """
    marker = "[RETRY]" if is_retry else ""
    try:
        raw = await asyncio.wait_for(
            generation_service.generate_meal_plan_async(
                prompt,
                recipe_system,
                "json",
                PARALLEL_SINGLE_MEAL_TOKENS,
                recipe_schema,
                model_purpose,
            ),
            timeout=timeout or PARALLEL_SINGLE_MEAL_TIMEOUT,
        )

        ingredients: list[dict] = raw.get("ingredients", [])
        recipe_steps: list[dict] = raw.get("recipe_steps", [])
        prep_time: int = (
            raw.get("prep_time_minutes") or session.cooking_time_minutes or 30
        )

        if not ingredients or (not recipe_steps and not fill_missing_steps):
            logger.warning(
                f"[PHASE-2-UNEXPECTED-EMPTY]{marker} index={index} | "
                f"ingredients={len(ingredients)} | steps={len(recipe_steps)}"
            )
            return None
        if not recipe_steps:
            recipe_steps = _fallback_recipe_steps(meal_name, ingredients, prep_time)

        # Calculate macros deterministically from ingredient list — ignore AI-reported values
        meal_macros = await nutrition_lookup.calculate_meal_macros(ingredients)

        # Scale ingredient quantities to match the session's calorie target
        scaled_macros = nutrition_lookup.scale_to_target(
            meal_macros,
            session.target_calories,
            reject_out_of_range=reject_on_scale_out_of_range,
        )
        if scaled_macros is None:
            logger.info(
                f"[PHASE-2-SCALE-REJECT]{marker} index={index} | "
                f"actual={meal_macros.calories:.0f} kcal | "
                f"target={session.target_calories} kcal | meal_name={meal_name}"
            )
            return None

        # Warn (don't reject) if protein ratio is >20% off the session's protein target
        if session.protein_target and session.protein_target > 0:
            protein_diff = (
                abs(scaled_macros.protein - session.protein_target)
                / session.protein_target
            )
            if protein_diff > 0.20:
                logger.warning(
                    f"[PHASE-2-PROTEIN-DRIFT]{marker} index={index} | "
                    f"scaled_protein={scaled_macros.protein:.1f}g | "
                    f"target={session.protein_target:.1f}g | drift={protein_diff:.0%}"
                )

        validated_macros = macro_validator.validate_deterministic(scaled_macros)

        # Update ingredient amounts in the raw response to reflect scaled quantities.
        # Also normalise unit to "g" — amount is now in grams regardless of original unit.
        scaled_ing_list = scaled_macros.ingredients
        for i, raw_ing in enumerate(ingredients):
            if i < len(scaled_ing_list):
                raw_ing["amount"] = round(scaled_ing_list[i].quantity_g)
                raw_ing["unit"] = "g"
                raw_ing["food_reference_id"] = scaled_ing_list[i].food_reference_id

        _log_ingredient_coverage(session, ingredients, meal_name, index, marker)

        logger.info(
            f"[PHASE-2-SUCCESS]{marker} index={index} | "
            f"model_purpose={model_purpose} | meal_name={meal_name}"
        )

        return MealSuggestion(
            id=f"sug_{uuid.uuid4().hex[:16]}",
            session_id=session.id,
            user_id=session.user_id,
            meal_name=meal_name,
            description="",
            meal_type=MealType(session.meal_type),
            macros=MacroEstimate(
                calories=validated_macros.calories,
                protein=validated_macros.protein,
                carbs=validated_macros.carbs,
                fat=validated_macros.fat,
            ),
            ingredients=[Ingredient(**ing) for ing in ingredients],
            recipe_steps=[RecipeStep(**step) for step in recipe_steps],
            prep_time_minutes=prep_time,
            confidence_score=0.85,
            origin_country=raw.get("origin_country"),
            cuisine_type=raw.get("cuisine_type", "International"),
            emoji=validate_emoji(raw.get("emoji")),
            english_name=meal_name,
        )

    except TimeoutError:
        logger.warning(
            f"[PHASE-2-TIMEOUT]{marker} index={index} | "
            f"model_purpose={model_purpose} | meal_name={meal_name}"
        )
        return None
    except Exception as e:
        logger.warning(
            f"[PHASE-2-FAIL]{marker} index={index} | "
            f"model_purpose={model_purpose} | error_type={type(e).__name__} | error={e}"
        )
        return None


def _log_ingredient_coverage(
    session: SuggestionSession,
    ingredients: list[dict],
    meal_name: str,
    index: int,
    marker: str,
) -> None:
    """Log a warning if user-specified ingredients are absent from the recipe."""
    if not session.ingredients or session.language != "en":
        return

    recipe_ing_names = " ".join(ing.get("name", "").lower() for ing in ingredients)
    meal_name_lower = meal_name.lower()

    def _found(user_ing: str) -> bool:
        ui = user_ing.lower().strip()
        prefix = ui[: max(4, len(ui) - 2)]
        return (
            ui in recipe_ing_names
            or prefix in recipe_ing_names
            or ui in meal_name_lower
            or prefix in meal_name_lower
        )

    missing = [ui for ui in session.ingredients if not _found(ui)]
    if missing:
        logger.warning(
            f"[PHASE-2-INGREDIENT-MISMATCH]{marker} index={index} | "
            f"missing={missing} | meal_name={meal_name}"
        )


_FALLBACK_NAME_SKIP = frozenset(
    {
        "a",
        "an",
        "and",
        "baked",
        "bowl",
        "fried",
        "garden",
        "grilled",
        "lemon",
        "marinara",
        "of",
        "plate",
        "power",
        "skillet",
        "steamed",
        "teriyaki",
        "the",
        "with",
    }
)


def fallback_selected_recipe(
    selected: dict,
    session: SuggestionSession,
) -> MealSuggestion:
    """Last-resort recipe so /recipes can still show ingredients and steps.

    Calories and macros stay on the selected Discover meal. Do not recompute.
    """
    localized = str(selected.get("name") or selected.get("english_name") or "").strip()
    english = str(selected.get("english_name") or selected.get("name") or localized).strip()
    meal_name = localized or english or "Meal"
    ingredients = [
        Ingredient(name=name, amount=0, unit="")
        for name in _ingredient_names_from_meal(english or meal_name)
    ]
    raw_steps = _fallback_recipe_steps(
        meal_name,
        [{"name": ing.name} for ing in ingredients],
        session.cooking_time_minutes or 20,
    )
    return MealSuggestion(
        id=f"sug_{uuid.uuid4().hex[:16]}",
        session_id=session.id,
        user_id=session.user_id,
        meal_name=meal_name,
        description="",
        meal_type=MealType(session.meal_type),
        macros=MacroEstimate(
            calories=int(round(float(selected.get("calories") or session.target_calories or 0))),
            protein=float(selected.get("protein") or session.protein_target or 0),
            carbs=float(selected.get("carbs") or session.carbs_target or 0),
            fat=float(selected.get("fat") or session.fat_target or 0),
        ),
        ingredients=ingredients,
        recipe_steps=[RecipeStep(**step) for step in raw_steps],
        prep_time_minutes=session.cooking_time_minutes or 20,
        confidence_score=0.35,
        cuisine_type="International",
        english_name=english or meal_name,
    )


def _ingredient_names_from_meal(meal_name: str) -> list[str]:
    tokens = [
        token
        for raw in meal_name.replace("-", " ").split()
        if (token := raw.strip().title()) and raw.lower() not in _FALLBACK_NAME_SKIP
    ]
    return tokens or [meal_name.strip() or "Meal"]


def _fallback_recipe_steps(
    meal_name: str, ingredients: list[dict], prep_time: int
) -> list[dict]:
    """Build simple steps when selected-recipe AI output omits instructions."""
    ingredient_names = [
        ing.get("name", "ingredient") for ing in ingredients if ing.get("name")
    ]
    ingredients_text = ", ".join(ingredient_names[:4]) or "the ingredients"
    first_duration = max(1, min(10, prep_time // 3 if prep_time else 5))
    second_duration = max(1, prep_time - first_duration if prep_time else 10)
    return [
        {
            "step": 1,
            "instruction": f"Prepare and portion {ingredients_text} for {meal_name}.",
            "duration_minutes": first_duration,
        },
        {
            "step": 2,
            "instruction": f"Cook until done, combine, and serve {meal_name}.",
            "duration_minutes": second_duration,
        },
    ]

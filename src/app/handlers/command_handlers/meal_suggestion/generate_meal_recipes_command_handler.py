"""
Handler for selected-discovery recipe generation.
"""

import logging
import uuid

from src.api.exceptions import (
    ExternalServiceException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_suggestion import GenerateMealRecipesCommand
from src.app.events.base import EventHandler, handles
from src.domain.model.meal_suggestion import MealSuggestion, SuggestionSession
from src.domain.services.meal_recommendation.ingredient_quantity_normalization import (
    normalize_ingredient_quantity,
)
from src.domain.services.meal_suggestion.suggestion_orchestration_service import (
    SuggestionOrchestrationService,
)
from src.domain.services.weekly_meal_planner.recipe_publication import (
    DraftIngredient,
    RecipeDraft,
    review_recipe_safety,
)

logger = logging.getLogger(__name__)


@handles(GenerateMealRecipesCommand)
class GenerateMealRecipesCommandHandler(
    EventHandler[GenerateMealRecipesCommand, list[MealSuggestion]]
):
    """Generate full recipes for selected discovery meals through the app layer."""

    def __init__(self, service: SuggestionOrchestrationService):
        self.service = service

    async def handle(self, command: GenerateMealRecipesCommand) -> list[MealSuggestion]:
        client_selected_meals = [
            _normalise_selected_discovery_meal(meal) for meal in command.selected_meals
        ]
        selected_meals = _order_selected_meals_by_ids(
            client_selected_meals, command.selected_meal_ids
        )

        if command.session_id and command.selected_meal_ids:
            discovery_session = await self.service._repo.get_session(command.session_id)
            if not discovery_session or discovery_session.user_id != command.user_id:
                if not selected_meals:
                    raise ResourceNotFoundException(
                        "Discovery session not found or expired",
                        error_code="DISCOVERY_SESSION_NOT_FOUND",
                    )
            else:
                selected_meals = _resolve_selected_meals_from_session(
                    discovery_session=discovery_session,
                    selected_meal_ids=command.selected_meal_ids,
                    client_selected_meals=client_selected_meals,
                )

        target_calories = (
            int(selected_meals[0]["calories"])
            if selected_meals
            else command.calorie_target or 500
        )
        session = SuggestionSession(
            id=f"recipe_{uuid.uuid4().hex[:16]}",
            user_id=command.user_id,
            meal_type=command.meal_type,
            meal_portion_type="main",
            target_calories=target_calories,
            ingredients=command.ingredients,
            cooking_time_minutes=command.cooking_time_minutes,
            language=command.language,
            cuisine_region=command.cuisine_region,
            protein_target=command.protein_target,
            carbs_target=command.carbs_target,
            fat_target=command.fat_target,
        )

        if selected_meals:
            try:
                recipes = (
                    await self.service._recipe_generator.generate_selected_recipes(
                        session, selected_meals
                    )
                )
            except RuntimeError as exc:
                raise ExternalServiceException(
                    "Could not generate recipes. Please retry.",
                    error_code="RECIPE_GENERATION_FAILED",
                    details={
                        "requested": len(selected_meals),
                        "generated": 0,
                        "reason": str(exc),
                    },
                ) from exc
            if len(recipes) != len(selected_meals):
                raise ExternalServiceException(
                    "Could not generate all selected recipes. Please retry.",
                    error_code="RECIPE_GENERATION_FAILED",
                    details={
                        "requested": len(selected_meals),
                        "generated": len(recipes),
                    },
                )
        else:
            recipes = await self.service._recipe_generator._phase2_generate_recipes(
                session,
                command.meal_names,
                "English",
                suggestion_count=len(command.meal_names),
                min_acceptable_override=1,
            )

        recipes = _drop_unsafe_recipes(recipes)
        if not recipes:
            raise ExternalServiceException(
                "Could not generate a safe recipe. Please retry.",
                error_code="RECIPE_GENERATION_UNSAFE",
            )

        if command.language != "en" and recipes:
            translation_service = self.service._recipe_generator._translation_service
            if translation_service:
                recipes = await translation_service.translate_meal_suggestions_batch(
                    recipes, command.language
                )

        return recipes


def _drop_unsafe_recipes(recipes: list[MealSuggestion]) -> list[MealSuggestion]:
    """Keep only the recipes a person can safely be told to cook.

    The food-safety half of the publication review, which reads the dish,
    its steps and its ingredient names. The nutrition half is left out on
    purpose: it rejects any unidentified ingredient over 10 g, and nothing
    in this path resolves ingredients yet, so it would discard every recipe
    including correct ones.
    """
    safe: list[MealSuggestion] = []
    for recipe in recipes:
        rejections = review_recipe_safety(_draft_from(recipe))
        if rejections:
            logger.warning(
                "Dropped generated recipe %r: %s",
                recipe.meal_name,
                "; ".join(rejections),
            )
            continue
        safe.append(recipe)
    return safe


def _draft_from(recipe: MealSuggestion) -> RecipeDraft:
    """Read a generated suggestion as the review rules expect it."""
    return RecipeDraft(
        name=recipe.meal_name or "",
        ingredients=tuple(
            DraftIngredient(name=line.name or "", grams=_grams(line))
            for line in recipe.ingredients or ()
        ),
        steps=tuple(
            step.instruction for step in recipe.recipe_steps or () if step.instruction
        ),
    )


def _grams(line) -> float:
    """The line's weight, or zero when it is counted rather than weighed."""
    amount = getattr(line, "amount", None)
    if amount is None:
        return 0.0
    normalized = normalize_ingredient_quantity(amount, getattr(line, "unit", "") or "")
    return float(normalized.amount) if normalized.dimension == "weight" else 0.0


def _normalise_selected_discovery_meal(meal: dict) -> dict:
    """Normalize mobile discovery JSON and stored session dicts to one shape."""
    macros = meal.get("macros") or {}
    return {
        "id": meal.get("id"),
        "name": meal.get("name") or meal.get("meal_name"),
        "english_name": meal.get("english_name")
        or meal.get("name")
        or meal.get("meal_name"),
        "calories": meal.get("calories") or macros.get("calories"),
        "protein": meal.get("protein") or macros.get("protein"),
        "carbs": meal.get("carbs") or macros.get("carbs"),
        "fat": meal.get("fat") or macros.get("fat"),
    }


def _order_selected_meals_by_ids(
    selected_meals: list[dict], selected_ids: list[str]
) -> list[dict]:
    """Order client-sent selected meal objects by selected_meal_ids."""
    if not selected_ids:
        return selected_meals
    by_id = {meal.get("id"): meal for meal in selected_meals if meal.get("id")}
    return [by_id[mid] for mid in selected_ids if mid in by_id]


def _resolve_selected_meals_from_session(
    discovery_session: SuggestionSession,
    selected_meal_ids: list[str],
    client_selected_meals: list[dict],
) -> list[dict]:
    by_id = {
        meal.get("id"): _normalise_selected_discovery_meal(meal)
        for meal in getattr(discovery_session, "discovery_meals", [])
        if meal.get("id")
    }
    client_by_id = {
        meal.get("id"): meal for meal in client_selected_meals if meal.get("id")
    }
    missing = [
        mid for mid in selected_meal_ids if mid not in by_id and mid not in client_by_id
    ]
    if missing:
        raise ValidationException(
            "Selected meal ids were not found in the discovery session",
            error_code="SELECTED_MEAL_NOT_FOUND",
            details={"missing_ids": missing},
        )
    return [by_id.get(mid) or client_by_id[mid] for mid in selected_meal_ids]

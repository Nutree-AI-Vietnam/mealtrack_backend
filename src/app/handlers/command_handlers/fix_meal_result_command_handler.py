"""Re-analyze a scanned meal photo using the user's correction text."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal.fix_meal_result_command import FixMealResultCommand
from src.app.events.base import EventHandler, handles
from src.app.events.meal.meal_events import publish_meal_event
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.meal_projection import MealProjection
from src.domain.parsers.vision_response_parser import VisionResponseParsingError
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.services.meal_calorie_service import (
    assert_fix_result_calorie_guardrail,
    effective_meal_calories,
    merge_fix_result_items,
    serialize_items_for_fix_prompt,
)
from src.domain.services.nutrition_calculation_service import (
    NutritionCalculationService,
)
from src.domain.strategies.meal_analysis_strategy import AnalysisStrategyFactory
from src.domain.utils.timezone_utils import format_iso_utc, utc_now

logger = logging.getLogger(__name__)

_SCAN_SOURCES = frozenset({"scanner", "scan", "scan_by_url"})
_FOOD_LABEL_SOURCES = frozenset({"food_label", "scan_food_label"})
_MAX_FIX_HISTORY = 10


def append_fix_result_correction(meal: Meal, correction: str) -> Meal:
    """Keep recent Fix result notes on the meal for review/export pipelines."""
    try:
        raw = json.loads(meal.raw_gpt_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    history = raw.get("_fix_result_corrections")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "correction": correction,
            "at": format_iso_utc(utc_now()),
            "edit_count_after": meal.edit_count,
        }
    )
    raw["_fix_result_corrections"] = history[-_MAX_FIX_HISTORY:]
    return replace(meal, raw_gpt_json=json.dumps(raw, ensure_ascii=False))


@handles(FixMealResultCommand)
class FixMealResultCommandHandler(EventHandler[FixMealResultCommand, Any]):
    """Fix a READY scanned meal in place using vision + correction text."""

    def __init__(
        self,
        uow_factory: Callable[[], AsyncUnitOfWorkPort],
        vision_service: Any,
        gpt_parser: Any,
        download_image_bytes: Callable[[str], Any],
        event_publisher: IntegrationEventPublisherPort | None = None,
        event_bus: Any | None = None,
        environment: str = "development",
    ):
        self.uow_factory = uow_factory
        self.vision_service = vision_service
        self.gpt_parser = gpt_parser
        self._download_image_bytes = download_image_bytes
        self.event_publisher = event_publisher
        self.event_bus = event_bus
        self.environment = environment

    async def handle(self, command: FixMealResultCommand) -> Any:
        async with self.uow_factory() as uow:
            meal = await uow.meals.find_by_id(
                command.meal_id, projection=MealProjection.FULL_WITH_TRANSLATIONS
            )
            if not meal:
                raise ResourceNotFoundException("Meal not found")
            if command.user_id and meal.user_id != command.user_id:
                raise AuthorizationException(
                    "You do not have permission to modify this meal"
                )
            if meal.status != MealStatus.READY:
                raise ValidationException(
                    "Meal must be in READY status to fix",
                    error_code="MEAL_NOT_READY",
                )

            source = (meal.source or "").strip().casefold()
            if source in _FOOD_LABEL_SOURCES:
                raise ValidationException(
                    "Food-label meals cannot use Fix result. Edit values manually.",
                    error_code="FIX_RESULT_UNSUPPORTED_SOURCE",
                )
            if source and source not in _SCAN_SOURCES:
                raise ValidationException(
                    "Only photo-scanned meals can use Fix result.",
                    error_code="FIX_RESULT_UNSUPPORTED_SOURCE",
                )

            image_url = getattr(getattr(meal, "image", None), "url", None)
            if not image_url:
                raise ValidationException(
                    "This meal has no photo to re-analyze.",
                    error_code="FIX_RESULT_NO_IMAGE",
                )

            old_items = list(
                (meal.nutrition.food_items if meal.nutrition else None) or []
            )
            old_calories = float(effective_meal_calories(meal))
            old_meal_date = (meal.created_at or utc_now()).date()
            dish_name = meal.dish_name

        try:
            image_bytes = await self._download_image_bytes(image_url)
        except Exception as exc:
            logger.warning(
                "fix_result_image_download_failed meal_id=%s err=%s",
                command.meal_id,
                exc,
            )
            raise ValidationException(
                "Could not load the meal photo for re-analysis.",
                error_code="FIX_RESULT_IMAGE_UNAVAILABLE",
            ) from exc

        if not image_bytes:
            raise ValidationException(
                "Could not load the meal photo for re-analysis.",
                error_code="FIX_RESULT_IMAGE_UNAVAILABLE",
            )

        strategy = AnalysisStrategyFactory.create_fix_result_strategy(
            correction=command.correction,
            current_items=serialize_items_for_fix_prompt(old_items),
            language=command.language,
        )
        try:
            vision_result = await self.vision_service.analyze_with_strategy(
                image_bytes, strategy
            )
        except Exception as exc:
            logger.exception("fix_result_vision_failed meal_id=%s", command.meal_id)
            raise ValidationException(
                "Could not re-analyze this meal. Please try again.",
                error_code="FIX_RESULT_VISION_FAILED",
            ) from exc

        if not self.gpt_parser.parse_is_food(vision_result):
            raise ValidationException(
                "The photo no longer looks like food. Meal was not changed.",
                error_code="FIX_RESULT_NOT_FOOD",
            )

        try:
            parsed_nutrition = self.gpt_parser.parse_to_nutrition(vision_result)
            new_dish_name = self.gpt_parser.parse_dish_name(vision_result)
        except VisionResponseParsingError as exc:
            raise ValidationException(
                "Could not understand the corrected analysis. Meal was not changed.",
                error_code="FIX_RESULT_PARSE_FAILED",
            ) from exc

        new_items = list(parsed_nutrition.food_items or [])
        if not new_items:
            raise ValidationException(
                "The corrected analysis returned no ingredients. Meal was not changed.",
                error_code="FIX_RESULT_EMPTY",
            )

        merged_items = merge_fix_result_items(old_items, new_items, command.correction)
        if not merged_items:
            raise ValidationException(
                "The corrected analysis returned no ingredients. Meal was not changed.",
                error_code="FIX_RESULT_EMPTY",
            )

        updated_nutrition = NutritionCalculationService().calculate_meal_total(
            merged_items
        )
        updated_nutrition.confidence_score = min(
            max(0.0, float(parsed_nutrition.confidence_score)), 1.0
        )
        # Ingredient composition changed — drop any whole-meal override.
        updated_nutrition.nutrition_override = None

        try:
            assert_fix_result_calorie_guardrail(
                old_calories=old_calories,
                new_calories=float(updated_nutrition.calories),
                correction=command.correction,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "FIX_RESULT_EMPTY_CALORIES":
                raise ValidationException(
                    "The corrected analysis had no calories. Meal was not changed.",
                    error_code="FIX_RESULT_EMPTY",
                ) from exc
            raise ValidationException(
                "The corrected estimate looked unrealistic and was not saved. "
                "Try a clearer correction or edit ingredients manually.",
                error_code="FIX_RESULT_CALORIE_JUMP",
            ) from exc

        final_dish_name = new_dish_name or dish_name or "Meal"

        async with self.uow_factory() as uow:
            meal = await uow.meals.find_by_id(
                command.meal_id, projection=MealProjection.FULL_WITH_TRANSLATIONS
            )
            if not meal:
                raise ResourceNotFoundException("Meal not found")
            if meal.status != MealStatus.READY:
                raise ValidationException(
                    "Meal must be in READY status to fix",
                    error_code="MEAL_NOT_READY",
                )

            updated_meal = append_fix_result_correction(
                meal.mark_edited(
                    nutrition=updated_nutrition,
                    dish_name=final_dish_name,
                ),
                command.correction,
            )
            saved_meal = await uow.meals.save(updated_meal)
            await uow.commit()

            meal_date = (saved_meal.created_at or utc_now()).date()
            await publish_meal_event(
                self.event_publisher,
                saved_meal,
                event_type="updated",
                environment=self.environment,
                meal_date=meal_date,
                language=command.language,
                event_bus=self.event_bus,
                old_meal_date=old_meal_date,
                source="fix_meal_result",
            )
            return saved_meal

"""Persisted meal translation orchestration."""

import asyncio
import inspect
import logging
from typing import Any, cast

from src.domain.model.meal import FoodItemTranslation, Meal, MealTranslation
from src.domain.model.nutrition import FoodItem
from src.domain.model.translation_result import TranslationOutcome, TranslationResult
from src.domain.ports.meal_translation_repository_port import (
    MealTranslationRepositoryPort,
)
from src.domain.services.localized_display_name import (
    already_in_target_language,
    keep_stored_display_name,
)
from src.domain.services.translation.text_translation_service import (
    TextTranslationService,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


class MealTranslationService:
    """Translate meal content and persist only complete provider results."""

    def __init__(
        self,
        translation_repo: MealTranslationRepositoryPort,
        text_translation_service: TextTranslationService,
    ) -> None:
        self._repo = translation_repo
        self._text_service = text_translation_service

    async def translate_meal(
        self,
        meal: Meal,
        dish_name: str,
        food_items: list[FoodItem],
        target_language: str,
        instructions: list | None = None,
    ) -> MealTranslation | None:
        """
        Translate a meal to target_language.

        Args:
            meal: Meal domain model (only meal_id is used).
            dish_name: English dish name.
            food_items: Food items whose names will be translated.
            target_language: ISO 639-1 target language code.
            instructions: Optional list of instruction dicts or strings.

        Returns:
            Saved MealTranslation, including an identity row when names are
            already in the request locale, or None on failure.
        """
        if target_language == "en":
            return None

        try:
            named_food_items = [item for item in food_items if item.name]

            # --- Cache check ---
            existing = await self._get_by_meal_and_language(
                meal.meal_id, target_language
            )
            if existing and existing.is_fully_cached(
                expected_ingredient_count=len(named_food_items),
                expected_instruction_count=len(instructions or []),
            ):
                logger.debug(
                    "Translation cache hit: meal=%s lang=%s",
                    meal.meal_id,
                    target_language,
                )
                return existing

            # --- Normalise instructions to List[dict] ---
            normalised_steps: list[dict] = []
            if instructions:
                for step in instructions:
                    if isinstance(step, dict):
                        normalised_steps.append(step)
                    elif isinstance(step, str):
                        normalised_steps.append(
                            {"instruction": step, "duration_minutes": None}
                        )

            ingredient_names = [item.name for item in named_food_items]
            instruction_texts = [s.get("instruction", "") for s in normalised_steps]

            # Build a single flat list so we use one provider call.
            # Layout: [dish_name, *ingredient_names, *instruction_texts]
            strings_to_translate = [dish_name] + ingredient_names + instruction_texts
            if all(
                already_in_target_language(text, target_language)
                for text in strings_to_translate
                if str(text).strip()
            ):
                logger.info(
                    "Meal translation skipped; names already in %s meal=%s",
                    target_language,
                    meal.meal_id,
                )
                saved = await self._save(
                    _identity_meal_translation(
                        meal_id=meal.meal_id,
                        language=target_language,
                        dish_name=dish_name,
                        named_food_items=named_food_items,
                        normalised_steps=normalised_steps,
                    )
                )
                return saved

            result, translated = await _translate_preserving_localized_names(
                self._text_service,
                strings_to_translate,
                target_language,
            )

            # --- Unpack results ---
            translated_dish_name = translated[0]

            n = len(ingredient_names)
            translated_ingredients = translated[1 : 1 + n]
            translated_food_items = [
                FoodItemTranslation(
                    food_item_id=str(item.id),
                    name=translated_name,
                )
                for item, translated_name in zip(
                    named_food_items, translated_ingredients, strict=False
                )
            ]

            m = len(instruction_texts)
            translated_instruction_texts = translated[1 + n : 1 + n + m]

            translated_instruction_list: list | None = None
            if normalised_steps:
                translated_instruction_list = []
                for orig_step, trans_text in zip(
                    normalised_steps, translated_instruction_texts, strict=False
                ):
                    translated_instruction_list.append(
                        {
                            "instruction": trans_text,
                            "duration_minutes": orig_step.get("duration_minutes"),
                        }
                    )

            translation = MealTranslation(
                meal_id=meal.meal_id,
                language=target_language,
                dish_name=translated_dish_name,
                food_items=translated_food_items,
                meal_instruction=translated_instruction_list,
                meal_ingredients=translated_ingredients,
                translated_at=utc_now(),
            )

            if result.outcome is not TranslationOutcome.TRANSLATED:
                logger.warning(
                    "Meal translation not persisted meal=%s lang=%s outcome=%s",
                    meal.meal_id,
                    target_language,
                    result.outcome.value,
                )
                return translation
            saved = await self._save(translation)
            logger.info(
                "Meal translation saved meal=%s lang=%s", meal.meal_id, target_language
            )
            return saved

        except Exception as exc:
            logger.warning(
                "Meal translation failed for meal=%s lang=%s error_type=%s",
                meal.meal_id,
                target_language,
                type(exc).__name__,
            )
            return None

    async def _get_by_meal_and_language(
        self, meal_id: str, language: str
    ) -> MealTranslation | None:
        method = self._repo.get_by_meal_and_language
        if inspect.iscoroutinefunction(method):
            return await cast(Any, method)(meal_id, language)
        return await asyncio.to_thread(cast(Any, method), meal_id, language)

    async def _save(self, translation: MealTranslation) -> MealTranslation:
        method = self._repo.save
        if inspect.iscoroutinefunction(method):
            return await cast(Any, method)(translation)
        return await asyncio.to_thread(cast(Any, method), translation)


def _identity_meal_translation(
    *,
    meal_id: str,
    language: str,
    dish_name: str,
    named_food_items: list[FoodItem],
    normalised_steps: list[dict],
) -> MealTranslation:
    """Persist stored localized names as the request-locale row."""
    return MealTranslation(
        meal_id=meal_id,
        language=language,
        dish_name=dish_name,
        food_items=[
            FoodItemTranslation(food_item_id=str(item.id), name=item.name)
            for item in named_food_items
        ],
        meal_instruction=(
            [
                {
                    "instruction": step.get("instruction", ""),
                    "duration_minutes": step.get("duration_minutes"),
                }
                for step in normalised_steps
            ]
            if normalised_steps
            else None
        ),
        meal_ingredients=[item.name for item in named_food_items],
        translated_at=utc_now(),
    )


async def _translate_texts(
    service: TextTranslationService, texts: list[str], target_language: str
) -> TranslationResult:
    """Translate an ordered batch from the canonical English source."""
    return await service.translate_texts(texts, "en", target_language)


async def _translate_preserving_localized_names(
    service: TextTranslationService,
    texts: list[str],
    target_language: str,
) -> tuple[TranslationResult, list[str]]:
    """Translate leftover English labels only. Keep already-localized names."""
    translate_indexes = [
        index
        for index, text in enumerate(texts)
        if not already_in_target_language(text, target_language)
    ]
    if not translate_indexes:
        passthrough = TranslationResult.passthrough(
            tuple(texts),
            source_language="en",
            target_language=target_language,
        )
        return passthrough, list(texts)

    result = await _translate_texts(
        service,
        [texts[index] for index in translate_indexes],
        target_language,
    )
    translated_batch = result.to_list()
    if result.outcome is not TranslationOutcome.TRANSLATED:
        translated_batch.extend(
            [texts[index] for index in translate_indexes[len(translated_batch) :]]
        )

    merged = list(texts)
    for offset, index in enumerate(translate_indexes):
        translated_name = (
            translated_batch[offset] if offset < len(translated_batch) else texts[index]
        )
        if keep_stored_display_name(
            stored=texts[index],
            translated=translated_name,
            language=target_language,
        ):
            translated_name = texts[index]
        merged[index] = translated_name
    return result, merged

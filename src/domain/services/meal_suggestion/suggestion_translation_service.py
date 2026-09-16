"""Translation orchestration for meal suggestions."""

import asyncio
import logging
from dataclasses import replace as dataclasses_replace

from src.domain.model.meal_suggestion import MealSuggestion
from src.domain.model.meal_suggestion.suggestion_translation_result import (
    SuggestionTranslationResult,
)
from src.domain.model.translation_result import TranslationOutcome, TranslationResult
from src.domain.services.translation.text_translation_service import (
    TextTranslationService,
)

logger = logging.getLogger(__name__)


async def _translate_texts(
    service: TextTranslationService, texts: list[str], target_language: str
) -> TranslationResult:
    """Translate an ordered batch from the canonical English source."""
    return await service.translate_texts(texts, "en", target_language)


class SuggestionTranslationService:
    """Translate suggestion fields while retaining persistence outcomes."""

    def __init__(self, text_translation_service: TextTranslationService) -> None:
        self._text_service = text_translation_service

    # ------------------------------------------------------------------
    # Public interface (matches TranslationService)
    # ------------------------------------------------------------------

    async def translate_meal_suggestion(
        self, suggestion: MealSuggestion, target_language: str
    ) -> MealSuggestion:
        """Translate a single suggestion; returns canonical on failure."""
        result = await self.translate_meal_suggestion_result(
            suggestion, target_language
        )
        if result.outcome is TranslationOutcome.UNAVAILABLE:
            return suggestion
        return result.suggestion

    async def translate_meal_suggestion_result(
        self, suggestion: MealSuggestion, target_language: str
    ) -> SuggestionTranslationResult:
        """Translate a suggestion while retaining the persistence outcome."""
        if target_language == "en":
            return SuggestionTranslationResult(
                dataclasses_replace(
                    suggestion, translation_outcome=TranslationOutcome.PASSTHROUGH
                ),
                TranslationOutcome.PASSTHROUGH,
            )
        try:
            return await self._translate_one(suggestion, target_language)
        except Exception as exc:
            logger.warning(
                "Suggestion translation failed id=%s lang=%s error_type=%s",
                suggestion.id,
                target_language,
                type(exc).__name__,
            )
            return SuggestionTranslationResult(
                dataclasses_replace(
                    suggestion, translation_outcome=TranslationOutcome.UNAVAILABLE
                ),
                TranslationOutcome.UNAVAILABLE,
            )

    async def translate_meal_suggestions_batch(
        self, suggestions: list[MealSuggestion], target_language: str
    ) -> list[MealSuggestion]:
        """Translate all suggestions concurrently; falls back per-item on failure."""
        if target_language == "en" or not suggestions:
            return suggestions

        results = await asyncio.gather(
            *[
                self.translate_meal_suggestion_result(s, target_language)
                for s in suggestions
            ],
            return_exceptions=True,
        )

        translated = []
        for original, result in zip(suggestions, results, strict=True):
            if isinstance(result, BaseException):
                translated.append(original)
            elif result.outcome is TranslationOutcome.UNAVAILABLE:
                translated.append(original)
            else:
                translated.append(result.suggestion)
        return translated

    async def translate_names(
        self, names: list[str], target_language: str
    ) -> list[str]:
        """Translate a list of meal names. Returns originals on failure."""
        if target_language == "en" or not names:
            return names
        try:
            result = await _translate_texts(self._text_service, names, target_language)
            return result.to_list()
        except Exception as exc:
            logger.warning("translate_names failed error_type=%s", type(exc).__name__)
            return names

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _translate_one(
        self, suggestion: MealSuggestion, target_language: str
    ) -> SuggestionTranslationResult:
        """
        Build one flat string list, call the provider once, then reconstruct the
        MealSuggestion dataclass with translated values.
        """
        # Keep the canonical English identity as the provider source. A selected
        # discovery meal may already have a localized display name, which is the
        # safe fallback if this request only partially translates.
        canonical_name = suggestion.english_name or suggestion.meal_name
        localized_name_fallback = (
            suggestion.meal_name
            if suggestion.english_name
            and suggestion.meal_name != suggestion.english_name
            else None
        )
        # Layout: [meal_name, description, *ingredient_names, *step_instructions]
        strings: list[str] = [canonical_name, suggestion.description or ""]
        n_ingredients = len(suggestion.ingredients)

        strings.extend(ing.name for ing in suggestion.ingredients)
        strings.extend(step.instruction for step in suggestion.recipe_steps)

        result = await _translate_texts(self._text_service, strings, target_language)
        translated = result.to_list()
        translated.extend(strings[len(translated) :])

        idx = 0
        translated_name = translated[idx]
        if (
            localized_name_fallback
            and result.outcome is not TranslationOutcome.TRANSLATED
        ):
            translated_name = localized_name_fallback
        idx += 1
        translated_description = translated[idx]
        idx += 1

        translated_ingredients = [
            dataclasses_replace(ing, name=translated[idx + i])
            for i, ing in enumerate(suggestion.ingredients)
        ]
        idx += n_ingredients

        translated_steps = [
            dataclasses_replace(step, instruction=translated[idx + i])
            for i, step in enumerate(suggestion.recipe_steps)
        ]

        return SuggestionTranslationResult(
            dataclasses_replace(
                suggestion,
                meal_name=translated_name,
                description=translated_description,
                ingredients=translated_ingredients,
                recipe_steps=translated_steps,
                translation_outcome=result.outcome,
            ),
            result.outcome,
        )

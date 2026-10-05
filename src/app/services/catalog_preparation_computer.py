"""Bounded provider computation for worker-owned catalog preparation."""

from src.app.services.catalog_meal_response_localizer import (
    _is_vietnamese_text,
    _unique_display_texts,
)
from src.domain.constants.languages import SUPPORTED_TRANSLATION_LANGUAGES
from src.domain.constants.translation_limits import iter_translation_batches
from src.domain.model.translation_result import TranslationOutcome
from src.domain.ports.catalog_preparation_port import (
    PreparationOutcome,
    PreparationResult,
    PreparationTask,
)


class CatalogPreparationComputer:
    def __init__(self, *, micronutrient_computer, translation_service):
        self.micronutrient_computer = micronutrient_computer
        self.translation_service = translation_service

    async def compute(self, preparation):
        if preparation.claim.task == PreparationTask.MICRONUTRIENTS:
            return await self.micronutrient_computer.compute(preparation)
        locale = preparation.claim.locale
        if locale not in SUPPORTED_TRANSLATION_LANGUAGES:
            return PreparationResult(
                PreparationOutcome.PERMANENT_FAILURE, error_code="unsupported_locale"
            )
        texts = _unique_display_texts((preparation.meal,))
        translations = {}
        try:
            for source in ("en", "vi"):
                selected = [
                    text
                    for text in texts
                    if ("vi" if _is_vietnamese_text(text) else "en") == source
                ]
                if source == locale:
                    translations.update({text: text for text in selected})
                    continue
                if selected and self.translation_service is None:
                    return PreparationResult(
                        PreparationOutcome.PERMANENT_FAILURE,
                        error_code="provider_unconfigured",
                    )
                batches = iter_translation_batches(selected)
                if sum(map(len, batches)) != len(selected):
                    return PreparationResult(
                        PreparationOutcome.PERMANENT_FAILURE,
                        error_code="translation_item_too_large",
                    )
                for batch in batches:
                    result = await self.translation_service.translate_texts(
                        batch, source, locale
                    )
                    if (
                        result.outcome != TranslationOutcome.TRANSLATED
                        or len(result.items) != len(batch)
                        or any(not text for text in result.items)
                    ):
                        return PreparationResult(
                            PreparationOutcome.RETRYABLE_FAILURE,
                            error_code="translation_unavailable",
                        )
                    translations.update(zip(batch, result.items, strict=True))
            return PreparationResult(
                PreparationOutcome.READY, {"translations": translations}
            )
        except Exception:
            return PreparationResult(
                PreparationOutcome.RETRYABLE_FAILURE,
                error_code="translation_unavailable",
            )

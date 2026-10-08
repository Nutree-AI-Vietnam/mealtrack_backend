"""Both food search buses share one translation cache per translator."""

import pytest

from src.api.dependencies import event_bus
from src.app.services.food_search_translation_cache import CachedTextTranslation
from src.domain.model.translation_result import TranslationOutcome, TranslationResult


class _Translator:
    def __init__(self):
        self.batches = []

    async def translate_texts(self, texts, source_language, target_language):
        self.batches.append(list(texts))
        return TranslationResult(
            tuple(f"{text} (vi)" for text in texts),
            TranslationOutcome.TRANSLATED,
            source_language,
            target_language,
        )


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(event_bus, "_food_search_translation", None)


def test_without_a_translator_there_is_nothing_to_cache():
    assert event_bus._food_search_translation_service(None) is None


@pytest.mark.asyncio
async def test_the_same_translator_gets_one_shared_cache():
    translator = _Translator()

    first = event_bus._food_search_translation_service(translator)
    second = event_bus._food_search_translation_service(translator)
    await first.translate_texts(["Rice"], "en", "vi")
    result = await second.translate_texts(["Rice"], "en", "vi")

    assert isinstance(first, CachedTextTranslation)
    assert second is first
    assert result.texts == ("Rice (vi)",)
    assert translator.batches == [["Rice"]]


@pytest.mark.asyncio
async def test_a_replaced_translator_gets_a_fresh_cache():
    old, new = _Translator(), _Translator()
    await event_bus._food_search_translation_service(old).translate_texts(
        ["Rice"], "en", "vi"
    )

    cache = event_bus._food_search_translation_service(new)
    await cache.translate_texts(["Rice"], "en", "vi")

    assert new.batches == [["Rice"]]

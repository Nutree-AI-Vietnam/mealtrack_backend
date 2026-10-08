"""Per-text translation cache in front of the food search translator."""

import pytest

from src.app.services.food_name_localizer import translate_food_texts
from src.app.services.food_search_translation_cache import CachedTextTranslation
from src.domain.model.translation_result import TranslationOutcome, TranslationResult
from src.domain.services.translation.text_translation_service import (
    TextTranslationService,
)

_GLOSSARY = {"Chicken breast": "Ức gà", "Beef pho": "Phở bò", "Rice": "Cơm"}


class _Translator:
    """Records each batch and answers from a fixed glossary."""

    def __init__(self, outcome=TranslationOutcome.TRANSLATED, glossary=None):
        self.outcome = outcome
        self.glossary = _GLOSSARY if glossary is None else glossary
        self.calls = []

    async def translate_texts(self, texts, source_language, target_language):
        self.calls.append((list(texts), source_language, target_language))
        if self.outcome is TranslationOutcome.UNAVAILABLE:
            return TranslationResult.unavailable(
                texts, source_language=source_language, target_language=target_language
            )
        return TranslationResult(
            tuple(self.glossary.get(text, text) for text in texts),
            self.outcome,
            source_language,
            target_language,
        )

    def batches(self):
        return [texts for texts, _, _ in self.calls]


class _Port:
    def __init__(self):
        self.batches = []

    async def translate_texts(self, texts, *, source_language, target_language):
        self.batches.append(list(texts))
        return TranslationResult(
            tuple(_GLOSSARY[text] for text in texts),
            TranslationOutcome.TRANSLATED,
            source_language,
            target_language,
        )


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return _Clock()


@pytest.mark.asyncio
async def test_repeat_texts_are_served_from_the_cache(clock):
    inner = _Translator()
    cache = CachedTextTranslation(inner, clock=clock)

    first = await cache.translate_texts(["Chicken breast", "Rice"], "en", "vi")
    second = await cache.translate_texts(["Rice", "Chicken breast"], "en", "vi")

    assert first.texts == ("Ức gà", "Cơm")
    assert second.texts == ("Cơm", "Ức gà")
    assert second.outcome is TranslationOutcome.TRANSLATED
    assert inner.batches() == [["Chicken breast", "Rice"]]


@pytest.mark.asyncio
async def test_only_texts_missing_from_the_cache_are_sent(clock):
    inner = _Translator()
    cache = CachedTextTranslation(inner, clock=clock)
    await cache.translate_texts(["Rice"], "en", "vi")

    result = await cache.translate_texts(
        ["Rice", "Beef pho", "", "Beef pho"], "en", "vi"
    )

    assert result.texts == ("Cơm", "Phở bò", "", "Phở bò")
    assert result.outcome is TranslationOutcome.TRANSLATED
    assert inner.calls[-1] == (["Beef pho"], "en", "vi")


@pytest.mark.asyncio
async def test_language_tags_share_an_entry_and_pairs_do_not(clock):
    inner = _Translator()
    cache = CachedTextTranslation(inner, clock=clock)
    await cache.translate_texts(["Rice"], "en-US", "vi_VN")

    same_pair = await cache.translate_texts(["Rice"], "EN", "vi")
    await cache.translate_texts(["Rice"], "en", "ja")

    assert (same_pair.source_language, same_pair.target_language) == ("en", "vi")
    assert inner.calls == [(["Rice"], "en", "vi"), (["Rice"], "en", "ja")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [TranslationOutcome.PARTIAL, TranslationOutcome.UNAVAILABLE]
)
async def test_incomplete_answers_are_not_remembered(clock, outcome):
    inner = _Translator(outcome=outcome)
    cache = CachedTextTranslation(inner, clock=clock)

    await cache.translate_texts(["Rice"], "en", "vi")
    result = await cache.translate_texts(["Rice"], "en", "vi")

    assert result.outcome is outcome
    assert inner.batches() == [["Rice"], ["Rice"]]


@pytest.mark.asyncio
async def test_blank_translation_falls_back_and_is_not_remembered(clock):
    inner = _Translator(glossary={"Rice": "", "Beef pho": "Phở bò"})
    cache = CachedTextTranslation(inner, clock=clock)

    result = await cache.translate_texts(["Rice", "Beef pho"], "en", "vi")
    await cache.translate_texts(["Rice", "Beef pho"], "en", "vi")

    assert result.texts == ("Rice", "Phở bò")
    assert result.outcome is TranslationOutcome.PARTIAL
    assert inner.batches() == [["Rice", "Beef pho"], ["Rice", "Beef pho"]]


@pytest.mark.asyncio
async def test_cached_texts_still_show_when_the_translator_is_unavailable(clock):
    inner = _Translator()
    cache = CachedTextTranslation(inner, clock=clock)
    await cache.translate_texts(["Rice"], "en", "vi")
    inner.outcome = TranslationOutcome.UNAVAILABLE

    result = await cache.translate_texts(["Rice", "Beef pho"], "en", "vi")

    assert result.texts == ("Cơm", "Beef pho")
    assert result.outcome is TranslationOutcome.PARTIAL


@pytest.mark.asyncio
async def test_entries_expire_after_the_ttl(clock):
    inner = _Translator()
    cache = CachedTextTranslation(inner, ttl_seconds=60, clock=clock)
    await cache.translate_texts(["Rice"], "en", "vi")

    clock.now += 59
    await cache.translate_texts(["Rice"], "en", "vi")
    clock.now += 1
    await cache.translate_texts(["Rice"], "en", "vi")

    assert inner.batches() == [["Rice"], ["Rice"]]


@pytest.mark.asyncio
async def test_least_recently_used_entry_is_evicted_past_the_limit(clock):
    inner = _Translator()
    cache = CachedTextTranslation(inner, max_entries=2, clock=clock)
    await cache.translate_texts(["Rice"], "en", "vi")
    await cache.translate_texts(["Beef pho"], "en", "vi")
    await cache.translate_texts(["Rice"], "en", "vi")

    await cache.translate_texts(["Chicken breast"], "en", "vi")
    await cache.translate_texts(["Rice"], "en", "vi")
    await cache.translate_texts(["Beef pho"], "en", "vi")

    assert inner.batches() == [
        ["Rice"],
        ["Beef pho"],
        ["Chicken breast"],
        ["Beef pho"],
    ]


@pytest.mark.asyncio
async def test_long_texts_are_translated_but_not_remembered(clock):
    long_query = "rice " * 60
    inner = _Translator(glossary={long_query: "cơm"})
    cache = CachedTextTranslation(inner, clock=clock)

    await cache.translate_texts([long_query], "en", "vi")
    result = await cache.translate_texts([long_query], "en", "vi")

    assert result.texts == ("cơm",)
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_same_language_and_blank_batches_go_straight_to_the_translator(clock):
    inner = _Translator()
    cache = CachedTextTranslation(inner, clock=clock)

    await cache.translate_texts(["Rice"], "en", "EN")
    await cache.translate_texts([], "en", "vi")
    await cache.translate_texts(["", ""], "en", "vi")

    assert inner.calls == [
        (["Rice"], "en", "EN"),
        ([], "en", "vi"),
        (["", ""], "en", "vi"),
    ]


@pytest.mark.asyncio
async def test_cached_answers_stay_cacheable_through_the_food_name_localizer(clock):
    port = _Port()
    cache = CachedTextTranslation(TextTranslationService(port), clock=clock)

    first = await translate_food_texts(
        ["Chicken breast", "Rice"], target_language="vi", translation_service=cache
    )
    second = await translate_food_texts(
        ["Rice"], target_language="vi", translation_service=cache
    )

    assert first.texts == ("Ức gà", "Cơm")
    assert second.texts == ("Cơm",)
    assert second.cacheable
    assert port.batches == [["Chicken breast", "Rice"]]

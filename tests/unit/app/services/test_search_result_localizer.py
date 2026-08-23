import pytest

from src.app.services.search_result_localizer import localize_search_result_names
from src.domain.model.translation_result import TranslationOutcome, TranslationResult


class _Translator:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def translate_texts(self, texts, source_language, target_language):
        self.calls.append(list(texts))
        return self.outcomes.pop(0)


@pytest.mark.asyncio
async def test_uses_name_vi_without_translation():
    localized, cacheable = await localize_search_result_names(
        [{"description": "Chicken", "name_vi": "Gà"}],
        language="vi",
        translation_service=_Translator([]),
    )
    assert localized[0]["description"] == "Gà"
    assert cacheable is True


@pytest.mark.asyncio
async def test_does_not_keep_english_chicken_for_vietnamese():
    translator = _Translator(
        [
            TranslationResult(
                ("Chicken",), TranslationOutcome.TRANSLATED, "en", "vi"
            )
        ]
    )
    localized, cacheable = await localize_search_result_names(
        [{"description": "Chicken", "name": "Chicken"}],
        language="vi",
        translation_service=translator,
    )
    assert localized[0]["description"] == "Gà"
    assert cacheable is True


@pytest.mark.asyncio
async def test_glossary_covers_common_english_search_names():
    localized, cacheable = await localize_search_result_names(
        [{"description": "Beef Broth", "name": "Beef Broth"}],
        language="vi",
        translation_service=None,
    )
    assert localized[0]["description"] == "Nước dùng bò"
    assert cacheable is True


@pytest.mark.asyncio
async def test_glossary_covers_prefetch_english_titles():
    localized, cacheable = await localize_search_result_names(
        [
            {"description": "Fried Rice"},
            {"description": "Rose Apple"},
            {"description": "Whole Milk"},
        ],
        language="vi",
        translation_service=None,
    )
    assert [item["description"] for item in localized] == [
        "Cơm chiên",
        "Táo hồng",
        "Sữa tươi",
    ]
    assert cacheable is True


@pytest.mark.asyncio
async def test_keeps_name_vi_when_sibling_rows_need_glossary():
    localized, cacheable = await localize_search_result_names(
        [
            {"description": "Chicken", "name_vi": "Gà ta"},
            {"description": "Beef Broth"},
        ],
        language="vi",
        translation_service=None,
    )
    assert [item["description"] for item in localized] == ["Gà ta", "Nước dùng bò"]
    assert cacheable is True

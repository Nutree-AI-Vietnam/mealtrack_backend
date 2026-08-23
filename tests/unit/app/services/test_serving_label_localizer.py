import pytest

from src.app.services.serving_label_localizer import localize_item_servings
from src.domain.model.translation_result import TranslationOutcome, TranslationResult


class _Translator:
    async def translate_texts(self, texts, source_language, target_language):
        mapping = {"cup, cooked, diced": "cốc, đã nấu, thái hạt lựu"}
        return TranslationResult(
            tuple(mapping.get(text, text) for text in texts),
            TranslationOutcome.TRANSLATED,
            source_language,
            target_language,
        )


@pytest.mark.asyncio
async def test_localize_item_servings_fills_display_description():
    items = [
        {
            "name": "Rice",
            "allowed_units": [
                {
                    "unit": "cup, cooked, diced",
                    "gram_weight": 158.0,
                    "description": "1 cup cooked, diced",
                }
            ],
        }
    ]

    await localize_item_servings(
        items,
        language="vi",
        translation_service=_Translator(),
    )

    labeled = items[0]["allowed_units"][0]
    assert labeled["display_description"] == "cốc, đã nấu, thái hạt lựu"
    assert labeled["unit"] == "cup, cooked, diced"
    assert labeled["description"] == "1 cup cooked, diced"


@pytest.mark.asyncio
async def test_localize_item_servings_uses_cached_phrase_before_translate():
    class _Boom:
        async def translate_texts(self, *args, **kwargs):
            raise AssertionError("translator should not run on cache hit")

    class _Uow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        class food_references:
            @staticmethod
            async def get_serving_phrase_translations(phrases, language):
                assert language == "vi"
                return {"cup, cooked, diced": "cốc, đã nấu, thái hạt lựu"}

    items = [
        {"allowed_units": [{"unit": "cup, cooked, diced", "description": "1 cup"}]}
    ]
    await localize_item_servings(
        items,
        language="vi",
        translation_service=_Boom(),
        uow_factory=lambda: _Uow(),
    )
    assert (
        items[0]["allowed_units"][0]["display_description"]
        == "cốc, đã nấu, thái hạt lựu"
    )


@pytest.mark.asyncio
async def test_localize_item_servings_does_not_persist_partial_translations():
    class _Partial:
        async def translate_texts(self, texts, source_language, target_language):
            return TranslationResult(
                tuple("lát" for _text in texts),
                TranslationOutcome.PARTIAL,
                source_language,
                target_language,
            )

    class _Uow:
        persisted = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        class food_references:
            @staticmethod
            async def get_serving_phrase_translations(phrases, language):
                return {}

            @staticmethod
            async def upsert_serving_phrase_translations(*_args, **_kwargs):
                _Uow.persisted = True

            @staticmethod
            async def apply_serving_name_vi(*_args, **_kwargs):
                _Uow.persisted = True

    items = [
        {
            "food_reference_id": 9,
            "allowed_units": [{"unit": "thin strip", "description": "1 thin strip"}],
        }
    ]
    await localize_item_servings(
        items,
        language="vi",
        translation_service=_Partial(),
        uow_factory=lambda: _Uow(),
        persist=True,
    )
    assert _Uow.persisted is False


@pytest.mark.asyncio
async def test_localize_item_servings_persists_canonical_vietnamese_serving():
    class _Uow:
        saved_labels = None
        saved_food_labels = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        class food_references:
            @staticmethod
            async def get_serving_phrase_translations(*_args, **_kwargs):
                return {}

            @staticmethod
            async def upsert_serving_phrase_translations(labels, language):
                _Uow.saved_labels = (labels, language)

            @staticmethod
            async def apply_serving_name_vi(food_reference_id, labels):
                _Uow.saved_food_labels = (food_reference_id, labels)

    items = [
        {
            "food_reference_id": 9,
            "allowed_units": [{"unit": "serving", "description": "1 serving"}],
        }
    ]

    await localize_item_servings(
        items,
        language="vi",
        translation_service=None,
        uow_factory=lambda: _Uow(),
        persist=True,
    )

    assert items[0]["allowed_units"][0]["display_description"] == "Khẩu phần"
    assert _Uow.saved_labels == ({"serving": "Khẩu phần"}, "vi")
    assert _Uow.saved_food_labels == (9, {"serving": "Khẩu phần"})

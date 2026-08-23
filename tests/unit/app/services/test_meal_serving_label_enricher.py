from types import SimpleNamespace

import pytest

from src.app.services.meal_serving_label_enricher import enrich_meal_serving_labels
from src.app.services.serving_label import serving_phrase_key
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


def _meal_with_leftover_serving():
    item = SimpleNamespace(
        food_reference_id=42,
        serving_options=[
            {
                "unit": "cup, cooked, diced",
                "gram_weight": 158.0,
                "description": "1 cup cooked, diced",
            }
        ],
    )
    return SimpleNamespace(nutrition=SimpleNamespace(food_items=[item]))


@pytest.mark.asyncio
async def test_enrich_meal_serving_labels_translates_leftovers():
    meal = _meal_with_leftover_serving()
    projections = {42: {"name": "Rice", "name_vi": "Cơm", "serving_labels": {}}}

    result = await enrich_meal_serving_labels(
        meal,
        projections,
        language="vi",
        translation_service=_Translator(),
        uow_factory=None,
    )

    assert (
        result[42]["serving_labels"][serving_phrase_key("cup, cooked, diced")]
        == "cốc, đã nấu, thái hạt lựu"
    )


@pytest.mark.asyncio
async def test_enrich_meal_serving_labels_skips_when_catalog_already_has_label():
    class _Boom:
        async def translate_texts(self, *args, **kwargs):
            raise AssertionError("translator should not run")

    meal = _meal_with_leftover_serving()
    projections = {
        42: {
            "name": "Rice",
            "serving_labels": {
                serving_phrase_key("cup, cooked, diced"): "cốc, đã nấu, thái hạt lựu"
            },
        }
    }

    result = await enrich_meal_serving_labels(
        meal,
        projections,
        language="vi",
        translation_service=_Boom(),
        uow_factory=None,
    )

    assert (
        result[42]["serving_labels"][serving_phrase_key("cup, cooked, diced")]
        == "cốc, đã nấu, thái hạt lựu"
    )


@pytest.mark.asyncio
async def test_enrich_meal_serving_labels_persists_canonical_serving():
    class _Uow:
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
            async def upsert_serving_phrase_translations(*_args, **_kwargs):
                return None

            @staticmethod
            async def apply_serving_name_vi(food_reference_id, labels):
                _Uow.saved_food_labels = (food_reference_id, labels)

    meal = SimpleNamespace(
        nutrition=SimpleNamespace(
            food_items=[
                SimpleNamespace(
                    food_reference_id=42,
                    serving_options=[
                        {
                            "unit": "serving",
                            "gram_weight": 98.0,
                            "description": "1 serving",
                        }
                    ],
                )
            ]
        )
    )

    result = await enrich_meal_serving_labels(
        meal,
        {42: {"serving_labels": {}}},
        language="vi",
        translation_service=None,
        uow_factory=lambda: _Uow(),
    )

    assert result[42]["serving_labels"][serving_phrase_key("serving")] == "Khẩu phần"
    assert _Uow.saved_food_labels == (42, {"serving": "Khẩu phần"})

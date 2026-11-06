"""A saved recipe goes into the week in the language of the person planning."""

import asyncio

import pytest

from src.app.services.saved_recipe_localizer import translate_saved_recipe
from src.domain.model.translation_result import TranslationOutcome, TranslationResult
from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot

VI = {
    "Hoi An Chicken Rice": "Cơm gà Hội An",
    "Free-range young hen": "Gà mái tơ thả vườn",
    "Turmeric": "Nghệ",
    "Clean the chicken with salt.": "Làm sạch gà với muối.",
    "Fragrant rice with shredded chicken.": "Cơm thơm với gà xé.",
}


class _Translator:
    def __init__(self, outcome=TranslationOutcome.TRANSLATED, error=None, delay=0.0):
        self.outcome = outcome
        self.error = error
        self.delay = delay
        self.calls = []

    async def translate_texts(self, texts, *, source_language, target_language):
        self.calls.append((list(texts), source_language, target_language))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return TranslationResult(
            tuple(VI.get(text, text) for text in texts),
            self.outcome,
            source_language,
            target_language,
        )


def _snapshot(**overrides):
    data = {
        "meal_name": "Hoi An Chicken Rice",
        "description": "Fragrant rice with shredded chicken.",
        "macros": {"protein": 40.0, "carbs": 60.0, "fat": 20.0},
        "servings": 4,
        "language": "en",
        "ingredients": [
            {
                "name": "Free-range young hen",
                "amount": 1,
                "unit": "piece",
                "grams": 1550,
            },
            {"name": "turmeric", "display_name": "Turmeric", "grams": 5},
        ],
        "recipe_steps": [{"instruction": "Clean the chicken with salt."}],
    }
    data.update(overrides)
    return SavedRecipeSnapshot.from_saved_suggestion("recipe_import_1", data)


@pytest.mark.asyncio
async def test_an_english_recipe_is_copied_in_vietnamese():
    translator = _Translator()

    copy = await translate_saved_recipe(_snapshot(), "vi", translator)

    assert copy.name == "Cơm gà Hội An"
    assert copy.description == "Cơm thơm với gà xé."
    assert [line.shown_name for line in copy.ingredients] == [
        "Gà mái tơ thả vườn",
        "Nghệ",
    ]
    assert [step.instruction for step in copy.steps] == ["Làm sạch gà với muối."]
    assert copy.language == "vi"
    # Pricing keys, amounts and macros stay; the English name keeps the photo.
    assert [line.name for line in copy.ingredients] == [
        "Free-range young hen",
        "turmeric",
    ]
    assert [line.grams for line in copy.ingredients] == [1550.0, 5.0]
    assert (copy.protein, copy.carbs, copy.fat, copy.servings) == (40.0, 60.0, 20.0, 4)
    assert copy.english_name == "Hoi An Chicken Rice"
    # One provider call for the whole recipe, from its language to the reader's.
    assert len(translator.calls) == 1
    assert translator.calls[0][1:] == ("en", "vi")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "language, snapshot",
    [
        ("en", _snapshot()),
        ("en-US", _snapshot()),
        ("vi", _snapshot(language=None)),
        (None, _snapshot()),
        ("xx", _snapshot()),
    ],
)
async def test_nothing_to_translate_keeps_the_copy(language, snapshot):
    translator = _Translator()

    assert await translate_saved_recipe(snapshot, language, translator) is snapshot
    assert translator.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "translator",
    [
        _Translator(outcome=TranslationOutcome.UNAVAILABLE),
        _Translator(error=RuntimeError("provider down")),
        _Translator(delay=1.0),
        None,
    ],
)
async def test_a_translation_that_fails_keeps_the_copy_as_saved(translator):
    snapshot = _snapshot()

    copy = await translate_saved_recipe(snapshot, "vi", translator, timeout_seconds=0.2)

    assert copy is snapshot

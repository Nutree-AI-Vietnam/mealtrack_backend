from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.app.services.catalog_meal_response_localizer import (
    clear_catalog_presentation_cache,
    localize_catalog_meals,
    localize_meal_recommendation_plan,
    localize_meal_recommendation_slot,
)
from src.domain.model.meal_recommendation import (
    CatalogMeal,
    CatalogMealIngredient,
    CatalogMealStep,
    PersistedMealRecommendationCandidate,
    PersistedMealRecommendationPlan,
    PersistedMealRecommendationSlot,
)
from src.domain.model.translation_result import TranslationOutcome, TranslationResult


class _Translator:
    def __init__(self, translations: dict[str, str]) -> None:
        self.translations = translations
        self.calls: list[tuple[list[str], str]] = []

    async def translate_texts(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> TranslationResult:
        self.calls.append((texts, target_lang))
        return TranslationResult(
            tuple(self.translations.get(text, text) for text in texts),
            TranslationOutcome.TRANSLATED,
            source_lang,
            target_lang,
        )


class _FailingTranslator:
    async def translate_texts(self, texts: list[str], target_lang: str) -> list[str]:
        raise RuntimeError("provider unavailable")


class _ShortTranslator:
    async def translate_texts(self, texts: list[str], target_lang: str) -> list[str]:
        return ["Cơm tô"]


class _CacheableTranslator:
    def __init__(self, translations: dict[str, str]) -> None:
        self.translations = translations
        self.calls: list[list[str]] = []

    async def translate_texts(
        self, texts: list[str], source_language: str, target_language: str
    ) -> TranslationResult:
        self.calls.append(list(texts))
        return TranslationResult(
            tuple(self.translations.get(text, text) for text in texts),
            TranslationOutcome.TRANSLATED,
            source_language,
            target_language,
        )


@pytest.fixture(autouse=True)
def _clear_catalog_translation_cache():
    clear_catalog_presentation_cache()
    yield
    clear_catalog_presentation_cache()


def _meal(meal_id: str, name: str = "Rice Bowl") -> CatalogMeal:
    return CatalogMeal(
        id=meal_id,
        catalog_key=f"key-{meal_id}",
        content_hash=f"{meal_id:0<64}"[:64],
        name=name,
        cuisine="Vietnamese",
        description="Warm rice with vegetables",
        image_url="https://example.com/meal.jpg",
        protein_g=Decimal("25"),
        carbs_g=Decimal("50"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("lunch",),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=7,
                display_name="Rice",
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )


def _slot(
    meal: CatalogMeal, alternative: CatalogMeal | None = None
) -> PersistedMealRecommendationSlot:
    selected = PersistedMealRecommendationCandidate(
        id="candidate-1",
        slot_id="slot-1",
        recommendation_date=date(2026, 7, 29),
        meal_type="lunch",
        catalog_meal_id=meal.id,
        candidate_rank=0,
        is_selected=True,
        score=Decimal("0.9"),
        selection_version=2,
        catalog_meal=meal,
    )
    alternatives = ()
    if alternative is not None:
        alternatives = (
            PersistedMealRecommendationCandidate(
                id="candidate-2",
                slot_id="slot-1",
                recommendation_date=date(2026, 7, 29),
                meal_type="lunch",
                catalog_meal_id=alternative.id,
                candidate_rank=1,
                is_selected=False,
                score=Decimal("0.8"),
                selection_version=2,
                catalog_meal=alternative,
            ),
        )
    return PersistedMealRecommendationSlot(
        id="slot-1",
        slot_date=date(2026, 7, 29),
        day_index=0,
        meal_type="lunch",
        catalog_meal_id=meal.id,
        target_calories=550,
        score=0.9,
        position=0,
        selection_version=2,
        selected=selected,
        alternatives=alternatives,
    )


@pytest.mark.asyncio
async def test_localize_slot_translates_allowlisted_text_once_and_preserves_contract():
    meal = _meal("meal-1")
    alternative = _meal("meal-2")
    translator = _Translator(
        {
            "Rice Bowl": "Cơm tô",
            "Vietnamese": "Việt Nam",
            "Warm rice with vegetables": "Cơm nóng cùng rau",
            "Rice": "Cơm",
        }
    )

    localized = await localize_meal_recommendation_slot(
        _slot(meal, alternative), language="vi", translation_service=translator
    )

    assert translator.calls == [
        (
            ["Rice Bowl", "Vietnamese", "Warm rice with vegetables", "Rice"],
            "vi",
        )
    ]
    assert localized.selected.catalog_meal.name == "Cơm tô"
    assert localized.selected.catalog_meal.cuisine == "Việt Nam"
    assert localized.selected.catalog_meal.description == "Cơm nóng cùng rau"
    assert localized.selected.catalog_meal.ingredients[0].display_name == "Cơm"
    assert localized.alternatives[0].catalog_meal.name == "Cơm tô"
    assert localized.catalog_meal_id == meal.id
    assert localized.target_calories == 550
    assert localized.selected.catalog_meal.ingredients[0].unit == "g"
    assert localized.selected.catalog_meal.calories == meal.calories
    assert meal.name == "Rice Bowl"


@pytest.mark.asyncio
async def test_localize_plan_skips_english_and_missing_translation_service():
    plan = PersistedMealRecommendationPlan(
        id="plan-1",
        user_id="user-1",
        status="active",
        timezone="Asia/Ho_Chi_Minh",
        start_date=date(2026, 7, 29),
        daily_calories=2000,
        operation="three_day",
        idempotency_key="key-1",
        request_fingerprint="f" * 64,
        slots=(_slot(_meal("meal-1")),),
    )
    translator = _Translator({})

    assert (
        await localize_meal_recommendation_plan(
            plan, language="en", translation_service=translator
        )
        is plan
    )
    assert (
        await localize_meal_recommendation_plan(
            plan, language="vi", translation_service=None
        )
        is plan
    )
    assert translator.calls == []


@pytest.mark.asyncio
async def test_localize_catalog_meals_translates_names_and_can_skip_ingredients():
    meal = _meal("meal-1")
    translator = _Translator(
        {
            "Rice Bowl": "Cơm tô",
            "Vietnamese": "Việt Nam",
            "Warm rice with vegetables": "Cơm nóng với rau",
            "Rice": "Gạo",
        }
    )

    without_ingredients = await localize_catalog_meals(
        (meal,),
        language="vi",
        translation_service=translator,
        include_ingredients=False,
    )
    with_ingredients = await localize_catalog_meals(
        (meal,),
        language="vi",
        translation_service=translator,
        include_ingredients=True,
    )

    assert without_ingredients[0].name == "Cơm tô"
    assert without_ingredients[0].cuisine == "Việt Nam"
    assert without_ingredients[0].description == "Cơm nóng với rau"
    assert without_ingredients[0].ingredients[0].display_name == "Rice"
    assert with_ingredients[0].ingredients[0].display_name == "Gạo"
    assert "Rice" not in translator.calls[0][0]
    assert "Rice" in translator.calls[1][0]


@pytest.mark.asyncio
async def test_localize_catalog_meals_keeps_vietnamese_recipe_copy_untouched():
    meal = replace(
        _meal("meal-vi"),
        name="Rau má xào tỏi",
        cuisine="Vietnamese",
        description="Xào rau má với tỏi.",
        summary="Món rau xanh đơn giản.",
        ingredients=(),
        steps=(
            CatalogMealStep(
                step_number=1,
                title="Chuẩn bị nguyên liệu",
                description="Rửa sạch rau má rồi để ráo.",
            ),
            CatalogMealStep(
                step_number=2,
                title="Xào rau má",
                description="Phi thơm tỏi, cho rau má vào xào vừa chín.",
            ),
        ),
    )
    translator = _Translator({"Vietnamese": "Ẩm thực Việt Nam"})

    localized = await localize_catalog_meals(
        (meal,), language="vi", translation_service=translator
    )

    assert translator.calls == [(["Vietnamese"], "vi")]
    assert localized[0].name == meal.name
    assert localized[0].description == meal.description
    assert localized[0].steps == meal.steps


@pytest.mark.asyncio
async def test_localize_catalog_meals_translates_allergen_guidance():
    meal = replace(
        _meal("meal-allergens"),
        allergens="Fish (fish sauce). Check the ingredient label of each product.",
    )
    translator = _Translator(
        {
            "Rice Bowl": "Cơm tô",
            "Vietnamese": "Việt Nam",
            "Warm rice with vegetables": "Cơm nóng với rau",
            "Rice": "Gạo",
            meal.allergens: "Cá (nước mắm). Kiểm tra nhãn thành phần của từng sản phẩm.",
        }
    )

    localized = await localize_catalog_meals(
        (meal,), language="vi", translation_service=translator
    )

    assert meal.allergens in translator.calls[0][0]
    assert localized[0].allergens == (
        "Cá (nước mắm). Kiểm tra nhãn thành phần của từng sản phẩm."
    )


@pytest.mark.asyncio
async def test_localize_catalog_meals_batches_page_then_reuses_cache():
    meals = (
        _meal("meal-1"),
        _meal("meal-2", name="Chicken Soup"),
    )
    translator = _CacheableTranslator(
        {
            "Rice Bowl": "Cơm tô",
            "Chicken Soup": "Súp gà",
            "Vietnamese": "Việt Nam",
            "Warm rice with vegetables": "Cơm nóng với rau",
        }
    )

    first = await localize_catalog_meals(
        meals,
        language="vi",
        translation_service=translator,
        include_ingredients=False,
    )
    second = await localize_catalog_meals(
        meals,
        language="vi",
        translation_service=translator,
        include_ingredients=False,
    )

    assert [meal.name for meal in first] == ["Cơm tô", "Súp gà"]
    assert [meal.name for meal in second] == ["Cơm tô", "Súp gà"]
    assert len(translator.calls) == 1
    assert translator.calls[0] == [
        "Rice Bowl",
        "Vietnamese",
        "Warm rice with vegetables",
        "Chicken Soup",
    ]


@pytest.mark.asyncio
async def test_localize_slot_returns_canonical_values_when_translation_fails():
    slot = _slot(_meal("meal-1"))

    localized = await localize_meal_recommendation_slot(
        slot, language="vi", translation_service=_FailingTranslator()
    )

    assert localized is slot


@pytest.mark.asyncio
async def test_localize_slot_falls_back_for_missing_translated_values():
    slot = _slot(_meal("meal-1"))

    localized = await localize_meal_recommendation_slot(
        slot, language="vi", translation_service=_ShortTranslator()
    )

    assert localized.selected.catalog_meal == slot.selected.catalog_meal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "locale,name,translated",
    [("en", "Cơm gà", "Chicken rice"), ("vi", "Chicken rice", "Cơm gà")],
)
async def test_prepared_translations_serve_both_source_languages_without_provider(
    locale, name, translated
):
    from src.app.services.catalog_meal_response_localizer import (
        localize_catalog_meal_names,
    )

    class Persisted:
        async def get_catalog_translations(self, meals, language):
            assert language == locale
            return {"meal": {name: translated}}

        async def translate_texts(self, *_):
            pytest.fail("Prepared GET presentation called a provider")

    meal = _meal("meal", name)
    result = await localize_catalog_meals(
        (meal,), language=locale, translation_service=Persisted()
    )
    names = await localize_catalog_meal_names(
        (meal,), language=locale, translation_service=Persisted()
    )
    assert result[0].name == names[0].name == translated
    assert result[0].protein_g == meal.protein_g


@pytest.mark.asyncio
async def test_missing_preparation_keeps_canonical_text_without_jobs_or_provider():
    class Pending:
        async def get_catalog_translations(self, *_):
            return {}

        async def translate_texts(self, *_):
            pytest.fail("Missing preparation called a provider")

    meal = _meal("meal")
    assert await localize_catalog_meals(
        (meal,), language="vi", translation_service=Pending()
    ) == (meal,)

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.domain.model.meal.meal import Meal, MealStatus
from src.domain.model.meal.meal_image import MealImage
from src.domain.model.meal.meal_translation_domain_models import (
    CURRENT_MEAL_TRANSLATION_VERSION,
    MealTranslation,
)
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import FoodItem
from src.domain.model.translation_result import TranslationOutcome, TranslationResult
from src.domain.services.meal_analysis.meal_translation_service import (
    MealTranslationService,
)
from src.domain.services.translation.text_translation_service import (
    TextTranslationService,
)


@pytest.fixture
def meal():
    return Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=MealStatus.PROCESSING,
        created_at=datetime.utcnow(),
        image=MealImage(
            image_id=str(uuid4()), format="jpeg", size_bytes=1024, url="https://x/y.jpg"
        ),
    )


@pytest.fixture
def food_items():
    return [
        FoodItem(
            id=str(uuid4()),
            name="Chicken breast",
            quantity=150,
            unit="g",
            macros=Macros(protein=31, carbs=0, fat=3.6),
        ),
        FoodItem(
            id=str(uuid4()),
            name="Brown rice",
            quantity=200,
            unit="g",
            macros=Macros(protein=5, carbs=46, fat=1.8),
        ),
    ]


@pytest.fixture
def repo():
    r = Mock()
    r.get_by_meal_and_language = Mock(return_value=None)
    r.save = Mock(side_effect=lambda t: t)
    return r


@pytest.fixture
def async_repo():
    r = Mock()
    r.get_by_meal_and_language = AsyncMock(return_value=None)
    r.save = AsyncMock(side_effect=lambda t: t)
    return r


@pytest.fixture
def text_translation_service():
    svc = AsyncMock(spec=TextTranslationService)
    svc.translate_texts = AsyncMock()
    return svc


@pytest.fixture
def service(repo, text_translation_service):
    return MealTranslationService(
        translation_repo=repo,
        text_translation_service=text_translation_service,
    )


@pytest.fixture
def async_repo_service(async_repo, text_translation_service):
    return MealTranslationService(
        translation_repo=async_repo,
        text_translation_service=text_translation_service,
    )


@pytest.mark.asyncio
async def test_translate_meal_skips_english(
    service, meal, food_items, text_translation_service, repo
):
    result = await service.translate_meal(meal, "Grilled chicken", food_items, "en")
    assert result is None
    text_translation_service.translate_texts.assert_not_called()
    repo.save.assert_not_called()


@pytest.mark.asyncio
async def test_translate_meal_uses_cache_when_fully_cached(
    service, meal, food_items, text_translation_service, repo
):
    cached = MealTranslation(
        meal_id=meal.meal_id,
        language="vi",
        dish_name="cached",
        food_items=[],
        meal_instruction=[{"instruction": "x", "duration_minutes": None}],
        meal_ingredients=["a", "b"],
    )
    repo.get_by_meal_and_language.return_value = cached

    result = await service.translate_meal(
        meal, "Grilled chicken", food_items, "vi", instructions=["Step"]
    )

    assert result == cached
    text_translation_service.translate_texts.assert_not_called()
    repo.save.assert_not_called()


@pytest.mark.asyncio
async def test_translate_meal_skips_when_names_already_in_target_language(
    service, meal, text_translation_service, repo
):
    food_items = [
        FoodItem(
            id=str(uuid4()),
            name="Cơm tấm",
            quantity=1,
            unit="dĩa",
            macros=Macros(protein=12, carbs=70, fat=4),
        ),
        FoodItem(
            id=str(uuid4()),
            name="Bì heo",
            quantity=1,
            unit="phần",
            macros=Macros(protein=6, carbs=2, fat=8),
        ),
    ]

    result = await service.translate_meal(
        meal,
        dish_name="Cơm tấm với sườn, bì, chả",
        food_items=food_items,
        target_language="vi",
    )

    assert result is not None
    assert result.dish_name == "Cơm tấm với sườn, bì, chả"
    assert result.meal_ingredients == ["Cơm tấm", "Bì heo"]
    assert result.food_items[0].name == "Cơm tấm"
    assert result.food_items[1].name == "Bì heo"
    text_translation_service.translate_texts.assert_not_called()
    repo.save.assert_called_once()


@pytest.mark.asyncio
async def test_translate_meal_calls_provider_and_saves(
    service, meal, food_items, text_translation_service, repo
):
    text_translation_service.translate_texts.return_value = TranslationResult(
        ("Gà nướng", "Ức gà", "Cơm gạo lứt", "Bước 1"),
        TranslationOutcome.TRANSLATED,
        "en",
        "vi",
    )

    result = await service.translate_meal(
        meal,
        dish_name="Grilled chicken",
        food_items=food_items,
        target_language="vi",
        instructions=["Step 1"],
    )

    assert result is not None
    assert result.dish_name == "Gà nướng"
    assert result.meal_ingredients == ["Ức gà", "Cơm gạo lứt"]
    assert [item.food_item_id for item in result.food_items] == [
        str(food_items[0].id),
        str(food_items[1].id),
    ]
    assert [item.name for item in result.food_items] == ["Ức gà", "Cơm gạo lứt"]
    assert result.meal_instruction == [
        {"instruction": "Bước 1", "duration_minutes": None}
    ]
    repo.save.assert_called_once()
    text_translation_service.translate_texts.assert_awaited_once()


@pytest.mark.asyncio
async def test_translate_meal_keeps_named_items_aligned_after_filtering(
    service, meal, food_items, text_translation_service
):
    unnamed_item = SimpleNamespace(id="unnamed", name=None)
    text_translation_service.translate_texts.return_value = TranslationResult(
        ("Bữa ăn", "Ức gà", "Cơm gạo lứt"),
        TranslationOutcome.TRANSLATED,
        "en",
        "vi",
    )

    result = await service.translate_meal(
        meal,
        dish_name="Meal",
        food_items=[unnamed_item, *food_items],
        target_language="vi",
    )

    assert result is not None
    assert [item.food_item_id for item in result.food_items] == [
        str(food_items[0].id),
        str(food_items[1].id),
    ]
    assert [item.name for item in result.food_items] == ["Ức gà", "Cơm gạo lứt"]


@pytest.mark.asyncio
async def test_translate_meal_awaits_async_translation_repo(
    async_repo_service, async_repo, meal, food_items, text_translation_service
):
    text_translation_service.translate_texts.return_value = TranslationResult(
        ("Gà nướng", "Ức gà", "Cơm gạo lứt"),
        TranslationOutcome.TRANSLATED,
        "en",
        "vi",
    )

    result = await async_repo_service.translate_meal(
        meal,
        dish_name="Grilled chicken",
        food_items=food_items,
        target_language="vi",
    )

    assert result is not None
    async_repo.get_by_meal_and_language.assert_awaited_once_with(meal.meal_id, "vi")
    async_repo.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_translate_meal_retranslates_pre_cutover_cache(
    service, meal, food_items, text_translation_service, repo
):
    repo.get_by_meal_and_language.return_value = MealTranslation(
        meal_id=meal.meal_id,
        language="vi",
        dish_name="Bữa ăn cũ",
        food_items=[],
        meal_ingredients=["Bread", "Pork"],
        translation_version=CURRENT_MEAL_TRANSLATION_VERSION - 1,
    )
    text_translation_service.translate_texts.return_value = TranslationResult(
        ("Bữa ăn", "Ức gà", "Cơm gạo lứt"),
        TranslationOutcome.TRANSLATED,
        "en",
        "vi",
    )

    result = await service.translate_meal(
        meal,
        dish_name="Meal",
        food_items=food_items,
        target_language="vi",
    )

    assert result is not None
    text_translation_service.translate_texts.assert_awaited_once()
    repo.save.assert_called_once()
    assert result.translation_version == CURRENT_MEAL_TRANSLATION_VERSION


@pytest.mark.asyncio
async def test_translate_meal_normalizes_instruction_dicts(
    service, meal, food_items, text_translation_service
):
    text_translation_service.translate_texts.return_value = TranslationResult(
        ("Gà nướng", "Ức gà", "Cơm gạo lứt", "Làm nóng chảo", "Nấu gà"),
        TranslationOutcome.TRANSLATED,
        "en",
        "vi",
    )

    result = await service.translate_meal(
        meal,
        dish_name="Grilled chicken",
        food_items=food_items,
        target_language="vi",
        instructions=[
            {"instruction": "Heat pan", "duration_minutes": 2},
            {"instruction": "Cook chicken", "duration_minutes": 10},
        ],
    )

    assert result.meal_instruction == [
        {"instruction": "Làm nóng chảo", "duration_minutes": 2},
        {"instruction": "Nấu gà", "duration_minutes": 10},
    ]


@pytest.mark.asyncio
async def test_translate_meal_fills_canonical_when_provider_returns_partial(
    service, meal, food_items, text_translation_service, caplog
):
    # Only dish name returned, rest should be padded with originals.
    text_translation_service.translate_texts.return_value = TranslationResult(
        ("Gà nướng",), TranslationOutcome.PARTIAL, "en", "vi"
    )

    result = await service.translate_meal(
        meal,
        dish_name="Grilled chicken",
        food_items=food_items,
        target_language="vi",
        instructions=["Step 1"],
    )

    assert result.dish_name == "Gà nướng"
    # Ingredients padded to original ingredient names
    assert result.meal_ingredients == [fi.name for fi in food_items]
    # Instruction padded to original
    assert result.meal_instruction == [
        {"instruction": "Step 1", "duration_minutes": None}
    ]
    assert "Meal translation not persisted" in caplog.text
    assert "outcome=partial" in caplog.text


@pytest.mark.asyncio
async def test_translate_meal_returns_none_on_exception(
    service, meal, food_items, text_translation_service, repo
):
    text_translation_service.translate_texts.side_effect = Exception("provider down")

    result = await service.translate_meal(
        meal,
        dish_name="Grilled chicken",
        food_items=food_items,
        target_language="vi",
        instructions=["Step 1"],
    )

    assert result is None
    repo.save.assert_not_called()

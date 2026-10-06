import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain.model.translation_result import TranslationOutcome
from src.infra.adapters.openai_translation_adapter import OpenAITranslationAdapter
from src.infra.services.ai.openai_structured_generation_result import (
    OpenAIStructuredGenerationResult,
)
from src.infra.services.ai.openai_translation_schemas import (
    OpenAITranslationBatch,
    OpenAITranslationItem,
)


@pytest.mark.asyncio
async def test_adapter_reconstructs_order_and_forces_non_storage():
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[
                OpenAITranslationItem(index=1, text="Riz"),
                OpenAITranslationItem(index=0, text="Poulet"),
            ]
        )
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(
        ["Chicken", "Rice"], source_language="en", target_language="fr"
    )

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert result.items == ("Poulet", "Riz")
    call = provider.generate_structured_result.await_args.kwargs
    assert call["store_responses"] is False
    assert "Translate food ingredients completely" in call["system_message"]


@pytest.mark.asyncio
async def test_adapter_repairs_missing_items_in_a_partial_batch():
    provider = AsyncMock()
    provider.generate_structured_result.side_effect = [
        OpenAIStructuredGenerationResult(
            parsed=OpenAITranslationBatch(
                items=[OpenAITranslationItem(index=1, text="Thịt heo")]
            ),
            incomplete=True,
        ),
        OpenAIStructuredGenerationResult(
            parsed=OpenAITranslationBatch(
                items=[
                    OpenAITranslationItem(index=0, text="Bánh mì"),
                    OpenAITranslationItem(index=2, text="Bông cải xanh"),
                ]
            )
        ),
    ]
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(
        ["Bread", "Pork", "Broccoli"], source_language="en", target_language="vi"
    )

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert result.items == ("Bánh mì", "Thịt heo", "Bông cải xanh")
    assert provider.generate_structured_result.await_count == 2
    repair_prompt = provider.generate_structured_result.await_args_list[1].kwargs[
        "prompt"
    ]
    assert '"index":0' in repair_prompt
    assert '"index":2' in repair_prompt


@pytest.mark.asyncio
async def test_adapter_repairs_an_unchanged_english_ingredient():
    provider = AsyncMock()
    provider.generate_structured_result.side_effect = [
        OpenAIStructuredGenerationResult(
            parsed=OpenAITranslationBatch(
                items=[
                    OpenAITranslationItem(index=0, text="Bread"),
                    OpenAITranslationItem(index=1, text="Thịt heo"),
                ]
            )
        ),
        OpenAIStructuredGenerationResult(
            parsed=OpenAITranslationBatch(
                items=[OpenAITranslationItem(index=0, text="Bánh mì")]
            )
        ),
    ]
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(
        ["Bread", "Pork"], source_language="en", target_language="vi"
    )

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert result.items == ("Bánh mì", "Thịt heo")
    assert provider.generate_structured_result.await_count == 2


@pytest.mark.asyncio
async def test_adapter_rejects_duplicate_indexes_without_partial_cache_result():
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[
                OpenAITranslationItem(index=0, text="Poulet"),
                OpenAITranslationItem(index=0, text="Riz"),
            ]
        )
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")
    result = await adapter.translate_texts(["Chicken", "Rice"], "en", "fr")
    assert result.outcome is TranslationOutcome.UNAVAILABLE
    assert result.items == ("Chicken", "Rice")


@pytest.mark.asyncio
async def test_adapter_never_marks_incomplete_full_index_output_translated():
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[
                OpenAITranslationItem(index=0, text="Poulet"),
                OpenAITranslationItem(index=1, text="Riz"),
            ]
        ),
        incomplete=True,
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(["Chicken", "Rice"], "en", "fr")

    assert result.outcome is TranslationOutcome.PARTIAL
    assert result.items == ("Poulet", "Riz")


@pytest.mark.asyncio
async def test_adapter_maps_translation_deadline_to_unavailable():
    provider = AsyncMock()

    async def wait_forever(**kwargs):
        await asyncio.sleep(1)

    provider.generate_structured_result.side_effect = wait_forever
    adapter = OpenAITranslationAdapter(
        provider=provider,
        model="translation-model",
        timeout_seconds=0.01,
    )

    result = await adapter.translate_texts(["Chicken"], "en", "fr")

    assert result.outcome is TranslationOutcome.UNAVAILABLE
    assert result.items == ("Chicken",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "candidate", "target"),
    [
        ("Coca-Cola 330 ml", "Pepsi 330 kg", "fr"),
        ("Nutella 20 g", "Nocilla 20 g", "fr"),
        ("Use 120 grams of rice", "Use 120 pounds of rice", "fr"),
        ("5 min", "5 kg", "fr"),
        ("Use 1 cup and 2 grams", "Usa 1 gramo y 2 tazas", "es"),
        ("Add {water} ml and {salt} g", "Añade {water} g y {salt} ml", "es"),
        ("Chicken", "Chicken", "fr"),
    ],
)
async def test_adapter_rejects_structurally_unsafe_or_unchanged_output(
    source, candidate, target
):
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[OpenAITranslationItem(index=0, text=candidate)]
        )
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts([source], "en", target)

    assert result.outcome is TranslationOutcome.PARTIAL
    assert result.items == (source,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "candidate", "target"),
    [
        ("Milk chocolate", "Chocolate con leche", "es"),
        ("Coca-Cola chicken", "Poulet Coca-Cola", "fr"),
        ("Nutella toast", "Toast au Nutella", "fr"),
        ("Beef burger", "Beef-Burger", "de"),
    ],
)
async def test_adapter_accepts_valid_loanwords_and_reordered_brands(
    source, candidate, target
):
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[OpenAITranslationItem(index=0, text=candidate)]
        )
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts([source], "en", target)

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert result.items == (candidate,)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "candidate", "target"),
    [
        ("Use 120 grams of rice", "Usa 120 gramos de arroz", "es"),
        ("Cook for 15 minutes", "Nấu trong 15 phút", "vi"),
        ("Add 0.5 cup", "加入 0.5杯", "zh"),
        ("Add 1 tablespoon", "Añade 1 cucharada", "es"),
        ("Add 1 tablespoon", "大さじ 1", "ja"),
        ("Wait 1 second", "Espera 1 segundo", "es"),
        ("Use 2 kilograms", "使用 2 公斤", "zh"),
        ("Use 2 cups", "Usa 2 tazas", "es"),
        ("Mix thoroughly", "充分混合", "zh"),
        ("Mix thoroughly", "十分に混ぜる", "ja"),
        ("Use 1 piece", "鶏肉を1個使う", "ja"),
        ("Use 1 slice", "使用 1 片", "zh"),
        ("Use 1 serving", "使用 1 份", "zh"),
        ("Use 1 piece", "Dùng 1 miếng", "vi"),
        ("Use 1 serving", "Dùng 1 phần", "vi"),
        ("Use 1 piece", "Usa 1 pieza", "es"),
        ("Use 1 slice", "Utilisez 1 tranche", "fr"),
        ("Use 1 serving", "Verwende 1 Portion", "de"),
        ("Use 2 slices", "Dùng 2 lát", "vi"),
        ("Use 1 cup", "Dùng 1 bát", "vi"),
        ("Grilled chicken rice bowl", "Bát cơm gà nướng", "vi"),
        ("Breakfast 1", "Frühstück 1", "de"),
        ("Ratio 1 to 2", "Proportion 1 à 2", "fr"),
        ("Ratio 1 to 2", "Proporción 1 a 2", "es"),
    ],
)
async def test_adapter_accepts_localized_equivalent_units(source, candidate, target):
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[OpenAITranslationItem(index=0, text=candidate)]
        )
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts([source], "en", target)

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert result.items == (candidate,)


@pytest.mark.parametrize(
    ("source", "candidate", "source_language", "target"),
    [
        ("2 quả cà chua", "2 tomatoes", "vi", "en"),
        ("Chiên chín vàng 2 mặt", "Fry until golden on both sides", "vi", "en"),
        ("1 thìa nước mắm", "1 tablespoon fish sauce", "vi", "en"),
        ("1/2 muỗng hạt nêm", "1/2 teaspoon seasoning", "vi", "en"),
        ("1 chén cơm", "1 bowl of rice", "vi", "en"),
        ("Cắt thành miếng vừa ăn", "Cut into bite-size pieces", "vi", "en"),
        ("2 tomatoes", "2 quả cà chua", "en", "vi"),
        ("Cắt mỏng thịt heo", "Slice the pork thinly", "vi", "en"),
        ("Nêm 1,5 muỗng cà phê hạt nêm", "Add 1.5 teaspoons seasoning", "vi", "en"),
        ("Dùng 1.500 g gạo", "Use 1,500 g of rice", "vi", "en"),
        ("Để khoảng 10 phút nữa", "Leave for about 10 more minutes", "vi", "en"),
        ("Cho canh ra tô", "Pour the soup into a bowl", "vi", "en"),
        ("Chiên mỗi mặt khoảng 2,3 phút", "Fry about 2-3 minutes per side", "vi", "en"),
        ("Đổ 1,5 lít nước", "Pour in 1.5 liters of water", "vi", "en"),
        ("Cho 20gr trà đen", "Add 20 g of black tea", "vi", "en"),
        ("Thêm ½ phần bánh Oreo", "Add 1/2 of the Oreo crumbs", "vi", "en"),
        ("1 phần khuấy với sữa", "Stir 1 portion with milk", "vi", "en"),
        ("Xếp bánh Oreo", "Arrange the Oreo cookies, then the Oreo cream", "vi", "en"),
        ("Cho vào tô 300ml nước", "Add 300 ml of water to a bowl", "vi", "en"),
        ("Rắc một thìa cà phê hạt nêm", "Sprinkle 1 teaspoon of seasoning", "vi", "en"),
        (
            "Múc ra chén, thêm 1 thìa dầu",
            "Ladle into a bowl, add a teaspoon of oil",
            "vi",
            "en",
        ),
        ("Thêm 1 thìa dầu", "Add 1 spoonful of oil", "vi", "en"),
        ("Add 1 tablespoon of oil", "Thêm một thìa canh dầu", "en", "vi"),
        (
            "Thêm 80gr đường. Dùng muỗng khuấy đều",
            "Add 80 g of sugar. Stir well with a spoon",
            "vi",
            "en",
        ),
        ("Cho 1 lít nước ra cốc", "Pour 1 liter of water into a cup", "vi", "en"),
        ("Thêm nửa muỗng cà phê muối", "Add half a teaspoon of salt", "vi", "en"),
        ("Thêm nửa muỗng cafe bột ngọt", "Add 1/2 teaspoon MSG", "vi", "en"),
        ("Chuẩn bị 7 cái ly sứ", "Prepare 7 medium-sized tea cups", "vi", "en"),
        (
            "Như ly đầu 1 cái, ly thứ hai 2 cái",
            "1 in the first cup, 2 in the second",
            "vi",
            "en",
        ),
    ],
)
def test_vietnamese_pairs_accept_classifier_and_spoon_variations(
    source, candidate, source_language, target
):
    adapter = OpenAITranslationAdapter(provider=None, model="translation-model")

    assert adapter._safe_output(source, candidate, target, source_language)


@pytest.mark.parametrize(
    ("source", "candidate", "source_language", "target"),
    [
        ("Ướp 15 phút", "Marinate for 20 minutes", "vi", "en"),
        ("Ướp 15 phút", "Marinate for 15 seconds", "vi", "en"),
        ("1 thìa nước mắm", "1 cup fish sauce", "vi", "en"),
        ("2 thìa nước mắm", "a spoon of fish sauce", "vi", "en"),
        ("1 thìa dầu", "Add 2 spoonfuls of oil", "vi", "en"),
        ("Chiên 2 mặt", "Fry 3 sides", "vi", "en"),
        ("Nêm 1,5 muỗng cà phê", "Add 15 teaspoons", "vi", "en"),
        ("Để 10 phút nữa", "Leave for 10 more seconds", "vi", "en"),
        ("Để 10 phút", "Leave for 10 minutes and 5 seconds", "vi", "en"),
        ("Chiên khoảng 2,5 phút", "Fry about 5 minutes", "vi", "en"),
        ("Đổ 1 lít nước", "Pour in 1 cup of water", "vi", "en"),
        ("Thêm ½ phần bánh", "Add 1/3 of the cake", "vi", "en"),
        ("Xếp bánh Oreo", "Arrange the cookies", "vi", "en"),
        ("Cho vào tô 300ml nước", "Add 300 g of water to a bowl", "vi", "en"),
        (
            "Rắc một thìa cà phê hạt nêm",
            "Sprinkle 2 teaspoons of seasoning",
            "vi",
            "en",
        ),
        ("Thêm nửa muỗng cà phê muối", "Add a teaspoon of salt", "vi", "en"),
        ("Chuẩn bị 7 cái ly", "Prepare 5 cups", "vi", "en"),
        ("Use 1 piece", "Usa 2 tazas", "en", "es"),
        ("Fry both sides twice", "Freír ambos lados 2 veces", "en", "es"),
    ],
)
def test_relaxed_check_still_rejects_changed_quantities(
    source, candidate, source_language, target
):
    adapter = OpenAITranslationAdapter(provider=None, model="translation-model")

    assert not adapter._safe_output(source, candidate, target, source_language)


@pytest.mark.asyncio
async def test_vietnamese_unchanged_answer_confirmed_by_repair_is_accepted():
    unchanged = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[OpenAITranslationItem(index=0, text="Chanh")]
        )
    )
    provider = AsyncMock()
    provider.generate_structured_result.side_effect = [unchanged, unchanged]
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(["Chanh"], "en", "vi")

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert provider.generate_structured_result.await_count == 2


@pytest.mark.asyncio
async def test_unchanged_answer_for_other_languages_stays_partial():
    unchanged = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[OpenAITranslationItem(index=0, text="Chicken")]
        )
    )
    provider = AsyncMock()
    provider.generate_structured_result.side_effect = [unchanged, unchanged]
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(["Chicken"], "en", "fr")

    assert result.outcome is TranslationOutcome.PARTIAL


@pytest.mark.asyncio
async def test_adapter_allows_reverse_translation_to_english_food_terms():
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[OpenAITranslationItem(index=0, text="chicken")]
        )
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(["gà"], "vi", "en")

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert result.items == ("chicken",)


@pytest.mark.asyncio
async def test_adapter_allows_invariant_only_brand_output():
    provider = AsyncMock()
    provider.generate_structured_result.return_value = OpenAIStructuredGenerationResult(
        parsed=OpenAITranslationBatch(
            items=[OpenAITranslationItem(index=0, text="Coca-Cola")]
        )
    )
    adapter = OpenAITranslationAdapter(provider=provider, model="translation-model")

    result = await adapter.translate_texts(["Coca-Cola"], "en", "fr")

    assert result.outcome is TranslationOutcome.TRANSLATED
    assert result.items == ("Coca-Cola",)

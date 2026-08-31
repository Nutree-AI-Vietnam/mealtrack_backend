import pytest

from src.domain.services.food_label_eval_loop import (
    FoodLabelEvalCase,
    FoodLabelEvalLoop,
    FoodLabelEvalObservation,
)


@pytest.mark.asyncio
async def test_food_label_eval_loop_passes_valid_cases():
    cases = [
        FoodLabelEvalCase(
            case_id="label-1",
            language="en",
            format_type="standard_us",
            expected_is_food_label=True,
            expected_product_name="Greek Yogurt",
            expected_serving_grams=170.0,
            expected_servings_per_package=1.0,
            expected_calories_per_serving=90.0,
            expected_macros={"protein": 16.0, "carbs": 6.0, "fat": 0.0},
            ai_payload={},
        ),
        FoodLabelEvalCase(
            case_id="label-2",
            language="en",
            format_type="non_label",
            expected_is_food_label=False,
            expected_product_name="",
            expected_serving_grams=0.0,
            expected_servings_per_package=0.0,
            expected_calories_per_serving=0.0,
            expected_macros={"protein": 0.0, "carbs": 0.0, "fat": 0.0},
            ai_payload={},
        ),
    ]

    async def mock_runner(case: FoodLabelEvalCase) -> FoodLabelEvalObservation:
        if case.expected_is_food_label:
            return FoodLabelEvalObservation(
                response={"ok": True},
                is_food_label=True,
                product_name="Chobani Plain Greek Yogurt",
                serving_grams=170.0,
                servings_per_package=1.0,
                calories_per_serving=90.0,
                macros={"protein": 16.0, "carbs": 6.0, "fat": 0.0},
                duration_ms=45.0,
                provider_calls=1,
                persisted_meal=False,
            )
        return FoodLabelEvalObservation(
            response={"ok": True},
            is_food_label=False,
            product_name=None,
            serving_grams=0.0,
            servings_per_package=0.0,
            calories_per_serving=0.0,
            macros={},
            duration_ms=25.0,
            provider_calls=1,
            persisted_meal=False,
        )

    eval_loop = FoodLabelEvalLoop()
    summary = await eval_loop.evaluate(cases, mock_runner)

    assert summary.case_count == 2
    assert summary.contract_pass_rate == 1.0
    assert summary.label_presence_accuracy == 1.0
    assert summary.field_match_rate == 1.0
    assert summary.calorie_accuracy_rate == 1.0
    assert summary.label_consistency_rate == 1.0
    assert summary.non_label_persisted_count == 0
    assert summary.catastrophic_outliers == 0

    eval_loop.enforce_gates(summary)


@pytest.mark.asyncio
async def test_food_label_eval_loop_fails_on_persisted_non_label():
    cases = [
        FoodLabelEvalCase(
            case_id="label-non-food",
            language="en",
            format_type="non_label",
            expected_is_food_label=False,
            expected_product_name="",
            expected_serving_grams=0.0,
            expected_servings_per_package=0.0,
            expected_calories_per_serving=0.0,
            expected_macros={"protein": 0.0, "carbs": 0.0, "fat": 0.0},
            ai_payload={},
        )
    ]

    async def mock_runner(case: FoodLabelEvalCase) -> FoodLabelEvalObservation:
        return FoodLabelEvalObservation(
            response={"ok": True},
            is_food_label=True,
            product_name="Fake Label",
            serving_grams=100.0,
            servings_per_package=1.0,
            calories_per_serving=200.0,
            macros={"protein": 10.0, "carbs": 20.0, "fat": 5.0},
            duration_ms=45.0,
            provider_calls=1,
            persisted_meal=True,  # Non-label persisted!
        )

    eval_loop = FoodLabelEvalLoop()
    summary = await eval_loop.evaluate(cases, mock_runner)

    assert summary.non_label_persisted_count == 1
    assert summary.catastrophic_outliers == 1
    with pytest.raises(AssertionError, match="non_label_persisted_count"):
        eval_loop.enforce_gates(summary)

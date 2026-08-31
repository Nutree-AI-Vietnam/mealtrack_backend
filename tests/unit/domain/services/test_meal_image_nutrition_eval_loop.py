import pytest

from src.domain.services.meal_image_nutrition_eval_loop import (
    MealImageEvalCase,
    MealImageEvalObservation,
    MealImageNutritionEvalLoop,
)


@pytest.mark.asyncio
async def test_meal_image_eval_loop_passes_valid_cases():
    cases = [
        MealImageEvalCase(
            case_id="case-1",
            language="en",
            category="atomic",
            expected_is_food=True,
            expected_dish_name="Chicken Breast",
            expected_foods=[
                {
                    "name": "Chicken Breast",
                    "quantity_g": 150,
                    "macros": {"protein": 45, "carbs": 0, "fat": 5},
                }
            ],
            expected_calorie_range=(200, 260),
            ai_payload={},
        ),
        MealImageEvalCase(
            case_id="case-2",
            language="en",
            category="non_food",
            expected_is_food=False,
            expected_dish_name="",
            expected_foods=[],
            expected_calorie_range=(0, 0),
            ai_payload={},
        ),
    ]

    async def mock_runner(case: MealImageEvalCase) -> MealImageEvalObservation:
        if case.expected_is_food:
            return MealImageEvalObservation(
                response={"ok": True},
                is_food=True,
                dish_name="Grilled Chicken Breast",
                foods=[
                    {
                        "name": "Chicken Breast",
                        "quantity_g": 150,
                        "macros": {"protein": 45, "carbs": 0, "fat": 5},
                    }
                ],
                total_calories=225.0,
                duration_ms=50.0,
                provider_calls=1,
            )
        return MealImageEvalObservation(
            response={"ok": True},
            is_food=False,
            dish_name=None,
            foods=[],
            total_calories=0.0,
            duration_ms=40.0,
            provider_calls=1,
        )

    eval_loop = MealImageNutritionEvalLoop()
    summary = await eval_loop.evaluate(cases, mock_runner)

    assert summary.case_count == 2
    assert summary.contract_pass_rate == 1.0
    assert summary.food_presence_accuracy == 1.0
    assert summary.mean_ingredient_f1 == 1.0
    assert summary.quantity_mape == 0.0
    assert summary.macro_wape == 0.0
    assert summary.catastrophic_outliers == 0

    eval_loop.enforce_gates(summary)


@pytest.mark.asyncio
async def test_meal_image_eval_loop_fails_on_catastrophic_outlier():
    cases = [
        MealImageEvalCase(
            case_id="case-bad",
            language="en",
            category="atomic",
            expected_is_food=True,
            expected_dish_name="Apple",
            expected_foods=[
                {
                    "name": "Apple",
                    "quantity_g": 150,
                    "macros": {"protein": 0.5, "carbs": 25, "fat": 0.3},
                }
            ],
            expected_calorie_range=(80, 110),
            ai_payload={},
        )
    ]

    async def mock_runner(case: MealImageEvalCase) -> MealImageEvalObservation:
        return MealImageEvalObservation(
            response={"ok": True},
            is_food=True,
            dish_name="Apple",
            foods=[
                {
                    "name": "Apple",
                    "quantity_g": 150,
                    "macros": {"protein": 0.5, "carbs": 25, "fat": 0.3},
                }
            ],
            total_calories=5000.0,  # Catastrophic outlier!
            duration_ms=30.0,
            provider_calls=1,
        )

    eval_loop = MealImageNutritionEvalLoop()
    summary = await eval_loop.evaluate(cases, mock_runner)

    assert summary.catastrophic_outliers == 1
    with pytest.raises(AssertionError, match="catastrophic_outliers"):
        eval_loop.enforce_gates(summary)

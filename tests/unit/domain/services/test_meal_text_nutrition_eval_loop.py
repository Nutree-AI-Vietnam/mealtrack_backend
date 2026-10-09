from types import SimpleNamespace

import pytest

from src.api.schemas.response.meal_responses import ServingUnitResponse
from src.domain.services.meal_text_nutrition_eval_loop import (
    TEXT_MICRO_UNITS,
    ParseTextEvalCase,
    ParseTextEvalObservation,
    ParseTextNutritionEvalLoop,
)


def _case(case_id: str = "en-potato-100g") -> ParseTextEvalCase:
    return ParseTextEvalCase(
        case_id=case_id,
        text="100g potato",
        language="en",
        expected_lookup_name="potato",
        expected_quantity_g=100,
        expected_source="fatsecret",
        expected_calorie_range=(60, 100),
        expected_candidate_id="potato",
    )


def _observation(source: str = "fatsecret", candidate: str | None = "potato"):
    item = SimpleNamespace(calories=77, data_source=source)
    return ParseTextEvalObservation(
        response=SimpleNamespace(items=[item]),
        extracted_lookup_name="potato",
        extracted_quantity_g=100,
        selected_candidate_id=candidate,
        provider_searches=1,
        provider_details=1,
        duration_ms=10,
    )


@pytest.mark.asyncio
async def test_eval_loop_reports_deterministic_metrics_and_passes_gates():
    loop = ParseTextNutritionEvalLoop()
    summary = await loop.evaluate([_case()], lambda _case: _ready(_observation()))

    assert summary.contract_pass_rate == 1.0
    assert summary.identity_quantity_pass_rate == 1.0
    assert summary.candidate_pass_rate == 1.0
    assert summary.common_reference_pass_rate == 1.0
    assert summary.catastrophic_outliers == 0
    assert summary.fallback_rate == 0.0
    assert summary.provider_search_calls == (1,)
    assert summary.provider_detail_calls == (1,)
    assert summary.latency_p95_ms is None
    assert summary.offline_fixture_latency_p95_ms == 10
    loop.enforce_gates(summary)


@pytest.mark.asyncio
async def test_eval_loop_marks_potato_calorie_outlier_as_gate_failure():
    loop = ParseTextNutritionEvalLoop()
    observation = _observation()
    observation = ParseTextEvalObservation(
        response=SimpleNamespace(
            items=[SimpleNamespace(calories=890, data_source="ai_estimate")]
        ),
        extracted_lookup_name=observation.extracted_lookup_name,
        extracted_quantity_g=observation.extracted_quantity_g,
        selected_candidate_id=observation.selected_candidate_id,
        provider_searches=observation.provider_searches,
        provider_details=observation.provider_details,
        duration_ms=observation.duration_ms,
    )
    summary = await loop.evaluate([_case()], lambda _case: _ready(observation))

    assert summary.catastrophic_outliers == 1
    with pytest.raises(ValueError, match="catastrophic_outliers"):
        loop.enforce_gates(summary)


@pytest.mark.asyncio
async def test_eval_loop_scores_every_item_and_whole_meal_calories_macros():
    case = ParseTextEvalCase(
        case_id="reviewed-two-item-meal",
        text="one banana and oats",
        language="en",
        expected_lookup_name="banana",
        expected_quantity_g=100,
        expected_source="ai_estimate",
        expected_calorie_range=(290, 320),
        reference_source_required=False,
        expected_items=(
            {"aliases": ["banana"], "quantity_g": 100},
            {"aliases": ["oats"], "quantity_g": 40},
        ),
        expected_meal_macros={"protein_g": [6, 8], "carbs_g": 50, "fat_g": 4},
    )
    response = SimpleNamespace(
        items=[
            SimpleNamespace(
                calories=110,
                data_source="ai_estimate",
                name="Banana",
                quantity=100,
                protein=1,
                carbs=23,
                fat=0.3,
                fiber=2,
                sugar=12,
            ),
            SimpleNamespace(
                calories=190,
                data_source="ai_estimate",
                name="Oats",
                quantity=40,
                protein=6,
                carbs=24,
                fat=3,
                fiber=4,
                sugar=1,
            ),
        ]
    )
    observation = ParseTextEvalObservation(
        response=response,
        extracted_lookup_name="banana",
        extracted_quantity_g=100,
        selected_candidate_id=None,
        provider_searches=0,
        provider_details=0,
        duration_ms=20,
        extracted_items=(
            {
                "lookup_name": "banana",
                "quantity_g": 100,
                "macros": {"protein_g": 1, "carbs_g": 23, "fat_g": 0.3},
            },
            {
                "lookup_name": "oats",
                "quantity_g": 40,
                "macros": {"protein_g": 6, "carbs_g": 24, "fat_g": 3},
            },
        ),
        evidence_kind="provider_observation",
        final_output_kind="handler_final_with_reviewed_reference",
        generation_duration_ms=12,
    )
    summary = await ParseTextNutritionEvalLoop().evaluate(
        [case], lambda _case: _ready(observation)
    )

    assert summary.evidence_kind == "provider_observation"
    assert summary.cases[0].food_count == 2
    assert summary.cases[0].total_calories_kcal == 300
    assert summary.cases[0].identity_pass is True
    assert summary.cases[0].quantity_pass is True
    assert summary.handler_calories_mae_kcal == 0
    assert summary.final_output_calories_mae_kcal == 0
    assert summary.raw_model_calories_mae_kcal is not None
    assert summary.cases[0].raw_model_food_count == 2
    assert summary.generation_latency_p50_ms == 12
    assert summary.provider_generation_latency_p50_ms == 12
    assert summary.handler_end_to_end_latency_p50_ms == 20


@pytest.mark.asyncio
async def test_known_missing_micro_scores_one_unknown_value_is_separate_and_masked_skips():
    assert TEXT_MICRO_UNITS == {
        "vitamin_a": "mcg",
        "vitamin_c": "mg",
        "vitamin_e": "mg",
        "calcium": "mg",
        "iron": "mg",
        "magnesium": "mg",
        "potassium": "mg",
        "sodium": "mg",
        "saturated_fat": "g",
        "added_sugar": "g",
    }
    case = ParseTextEvalCase(
        case_id="reviewed-two-micros",
        text="food plus side",
        language="en",
        expected_lookup_name="food",
        expected_quantity_g=100,
        expected_source="ai_estimate",
        expected_calorie_range=(100, 200),
        reference_source_required=False,
        expected_items=(
            {"aliases": ["food"], "quantity_g": 100},
            {"aliases": ["side"], "quantity_g": 50},
        ),
        expected_meal_micros={
            "iron": {"status": "known", "unit": "mg", "value": [2.5, 2.5]},
            "vitamin_c": {"status": "unknown", "unit": "mg"},
            "vitamin_e": {"status": "masked", "unit": "mg"},
        },
    )
    items = [
        SimpleNamespace(
            calories=90,
            data_source="ai_estimate",
            name="Food",
            quantity=100,
            protein=4,
            carbs=10,
            fat=2,
            source_snapshot={"extra_nutrients": {"iron_mg": 2, "vitamin_c_mg": 20}},
        ),
        SimpleNamespace(
            calories=40,
            data_source="ai_estimate",
            name="Side",
            quantity=0.5,
            unit="cup",
            allowed_units=[ServingUnitResponse(unit="cup", gram_weight=100)],
            protein=1,
            carbs=5,
            fat=0,
            source_snapshot={"extra_nutrients": {"iron_mg": 1}},
        ),
    ]
    observation = ParseTextEvalObservation(
        response=SimpleNamespace(items=items),
        extracted_lookup_name="food",
        extracted_quantity_g=100,
        selected_candidate_id=None,
        provider_searches=0,
        provider_details=0,
        duration_ms=10,
        final_output_kind="handler_final_with_reviewed_reference",
        extracted_items=(
            {
                "lookup_name": "food",
                "quantity_g": 100,
                "micros": {"iron": 1, "vitamin_c": 20},
            },
            {
                "lookup_name": "side",
                "quantity_g": 50,
                "micros": {"iron": None, "vitamin_c": None},
            },
        ),
        evidence_kind="provider_observation",
    )
    summary = await ParseTextNutritionEvalLoop().evaluate(
        [case], lambda _case: _ready(observation)
    )

    assert summary.raw_model_micro_coverage == {"iron": 0.0}
    assert summary.raw_model_micro_normalized_mae == {"iron": 1.0}
    assert summary.micro_coverage == {"iron": 1.0}
    assert summary.micro_normalized_mae == {"iron": 0.0}
    assert summary.final_output_micro_coverage == {"iron": 1.0}
    assert summary.handler_micro_coverage == {"iron": 1.0}
    assert summary.handler_micro_normalized_mae == {"iron": 0.0}
    assert summary.unknown_micro_population == {"vitamin_c": 1}
    assert "vitamin_e" not in summary.raw_model_micro_coverage
    assert summary.handler_calories_mae_kcal == 0


@pytest.mark.asyncio
async def test_contract_fixtures_do_not_publish_quality_or_token_metrics():
    case = _case()
    observation = _observation()
    summary = await ParseTextNutritionEvalLoop().evaluate(
        [case], lambda _case: _ready(observation)
    )

    assert summary.evidence_kind == "offline_contract_only"
    assert summary.calories_mae_kcal is None
    assert summary.raw_model_micro_coverage == {}
    assert summary.input_tokens is None
    assert summary.output_tokens is None


async def _ready(observation):
    return observation

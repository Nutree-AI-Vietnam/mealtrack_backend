import pytest

from src.domain.services.meal_analysis.prompt_eval_loop import (
    MICRO_UNITS,
    PromptEvalCase,
    PromptEvalLoop,
    PromptEvalResult,
)


def _valid_payload() -> dict:
    return {
        "structured_data": {
            "dish_name": "Chicken Rice",
            "foods": [
                {
                    "name": "Chicken",
                    "quantity_g": 120,
                    "macros": {"protein_g": 30, "carbs_g": 0, "fat_g": 6},
                }
            ],
            "confidence": 0.9,
        }
    }


def _invalid_payload() -> dict:
    return {
        "structured_data": {"dish_name": "Broken", "foods": [{"name": "No macros"}]}
    }


def _legacy_alias_payload() -> dict:
    return {
        "structured_data": {
            "dish_name": "Legacy Chicken Rice",
            "foods": [
                {
                    "name": "Chicken",
                    "quantity": 120,
                    "unit": "g",
                    "macros": {"protein": 30, "carbs": 0, "fat": 6},
                }
            ],
            "confidence": 0.9,
        }
    }


def _legacy_alias_payload_without_unit() -> dict:
    return {
        "structured_data": {
            "dish_name": "Legacy Chicken Rice",
            "foods": [
                {
                    "name": "Chicken",
                    "quantity": 120,
                    "macros": {"protein": 30, "carbs": 0, "fat": 6},
                }
            ],
            "confidence": 0.9,
        }
    }


def test_rank_candidates_prefers_higher_parse_success_then_lower_token_cost():
    loop = PromptEvalLoop()
    cases = [
        PromptEvalCase(case_id="ok-1", response_payload=_valid_payload()),
        PromptEvalCase(case_id="ok-2", response_payload=_valid_payload()),
    ]
    candidates = {
        "long": "x" * 1200,
        "short": "x" * 200,
    }

    ranked = loop.rank_candidates(candidates, cases)

    assert ranked[0].name == "short"
    assert ranked[0].parse_success_rate == 1.0
    assert ranked[1].name == "long"


def test_rank_candidates_penalizes_parse_failures():
    loop = PromptEvalLoop()
    cases = [
        PromptEvalCase(case_id="ok", response_payload=_valid_payload()),
        PromptEvalCase(case_id="bad", response_payload=_invalid_payload()),
    ]
    candidates = {
        "candidate-a": "x" * 250,
        "candidate-b": "x" * 250,
    }

    ranked = loop.rank_candidates(
        candidates,
        cases,
        case_overrides={
            "candidate-a": {"bad": _valid_payload()},
        },
    )

    assert ranked[0].name == "candidate-a"
    assert ranked[0].parse_success_rate == 1.0
    assert ranked[1].parse_success_rate == 0.5


def test_enforce_thresholds_raises_on_regression():
    loop = PromptEvalLoop()
    cases = [PromptEvalCase(case_id="bad", response_payload=_invalid_payload())]
    candidates = {"candidate": "x" * 300}
    result = loop.rank_candidates(candidates, cases)[0]

    try:
        loop.enforce_thresholds(
            result, min_parse_success_rate=0.9, max_prompt_tokens=100
        )
        raise AssertionError("Expected threshold validation to fail")
    except ValueError as exc:
        message = str(exc)
        assert "parse_success_rate" in message or "prompt_tokens_estimate" in message


def test_schema_invalid_payload_has_lower_validation_rate():
    """Candidate with validation-failing payloads scores lower validation_success_rate."""
    loop = PromptEvalLoop()
    invalid_quantity_payload = {
        "structured_data": {
            "dish_name": "Broken",
            "foods": [
                {
                    "name": "Huge",
                    "quantity_g": 150000,
                    "macros": {"protein_g": 500, "carbs_g": 1000, "fat_g": 200},
                }
            ],
        }
    }
    cases = [
        PromptEvalCase(case_id="valid", response_payload=_valid_payload()),
        PromptEvalCase(case_id="invalid", response_payload=invalid_quantity_payload),
    ]
    candidates = {"candidate": "x" * 200}
    ranked = loop.rank_candidates(candidates, cases)
    assert ranked[0].validation_success_rate == pytest.approx(0.5)


def test_alias_only_legacy_payload_lowers_validation_rate():
    """Prompt validation must match parser preflight rejection for legacy aliases."""
    loop = PromptEvalLoop()
    cases = [
        PromptEvalCase(case_id="valid", response_payload=_valid_payload()),
        PromptEvalCase(case_id="legacy", response_payload=_legacy_alias_payload()),
    ]
    candidates = {"candidate": "x" * 200}

    ranked = loop.rank_candidates(candidates, cases)

    assert ranked[0].parse_success_rate == pytest.approx(0.5)
    assert ranked[0].validation_success_rate == pytest.approx(0.5)


def test_alias_only_legacy_payload_without_unit_lowers_validation_rate():
    """Prompt validation must reject alias-only payloads accepted by schema aliases."""
    loop = PromptEvalLoop()
    cases = [
        PromptEvalCase(case_id="valid", response_payload=_valid_payload()),
        PromptEvalCase(
            case_id="legacy",
            response_payload=_legacy_alias_payload_without_unit(),
        ),
    ]
    candidates = {"candidate": "x" * 200}

    ranked = loop.rank_candidates(candidates, cases)

    assert ranked[0].parse_success_rate == pytest.approx(0.5)
    assert ranked[0].validation_success_rate == pytest.approx(0.5)


def test_enforce_thresholds_fails_on_validation_rate():
    """enforce_thresholds raises when validation_success_rate below threshold."""
    loop = PromptEvalLoop()
    result = PromptEvalResult(
        name="candidate",
        parse_success_rate=1.0,
        validation_success_rate=0.4,
        prompt_tokens_estimate=100.0,
        score=100.0,
    )
    with pytest.raises(ValueError, match="validation_success_rate"):
        loop.enforce_thresholds(
            result,
            min_parse_success_rate=0.5,
            max_prompt_tokens=200,
            min_validation_success_rate=0.8,
        )


def test_valid_payload_achieves_full_validation_rate():
    """All-valid cases yield validation_success_rate=1.0."""
    loop = PromptEvalLoop()
    cases = [
        PromptEvalCase(case_id="ok1", response_payload=_valid_payload()),
        PromptEvalCase(case_id="ok2", response_payload=_valid_payload()),
    ]
    candidates = {"candidate": "x" * 200}
    ranked = loop.rank_candidates(candidates, cases)
    assert ranked[0].validation_success_rate == pytest.approx(1.0)


def test_provider_observations_score_candidate_specific_whole_meal_results():
    loop = PromptEvalLoop()
    case = PromptEvalCase(
        case_id="reviewed-rice-egg",
        expected_foods=(
            {"aliases": ["rice"], "quantity_g": [190, 210]},
            {"aliases": ["egg"], "quantity_g": 50},
        ),
        expected_calories_kcal=(320, 330),
        expected_macros={"protein_g": (9, 12), "carbs_g": 50, "fat_g": (5, 7)},
    )
    baseline = {
        "structured_data": {
            "foods": [
                {
                    "name": "Rice",
                    "quantity_g": 200,
                    "macros": {"protein_g": 4, "carbs_g": 56, "fat_g": 1},
                },
                {
                    "name": "Egg",
                    "quantity_g": 50,
                    "macros": {"protein_g": 6, "carbs_g": 1, "fat_g": 5},
                },
            ]
        }
    }
    repeated_baseline = {
        "structured_data": {
            "foods": [
                {
                    "name": "Rice",
                    "quantity_g": 200,
                    "macros": {"protein_g": 4, "carbs_g": 60, "fat_g": 1},
                },
                {
                    "name": "Egg",
                    "quantity_g": 50,
                    "macros": {"protein_g": 6, "carbs_g": 1, "fat_g": 5},
                },
            ]
        }
    }
    candidate = {
        "structured_data": {
            "foods": [
                {
                    "name": "Rice",
                    "quantity_g": 300,
                    "macros": {"protein_g": 4, "carbs_g": 56, "fat_g": 1},
                },
                {
                    "name": "Egg",
                    "quantity_g": 50,
                    "macros": {"protein_g": 6, "carbs_g": 1, "fat_g": 5},
                },
            ]
        }
    }
    results = {
        result.name: result
        for result in loop.rank_candidates(
            {"baseline": "prompt A", "candidate": "prompt B"},
            [case],
            case_overrides={
                "baseline": {
                    case.case_id: [
                        {
                            "response_payload": repeated_baseline,
                            "duration_ms": 100,
                            "evidence_kind": "provider_observation",
                        },
                        {
                            "response_payload": baseline,
                            "duration_ms": 120,
                            "evidence_kind": "provider_observation",
                        },
                    ]
                },
                "candidate": {
                    case.case_id: [
                        {
                            "response_payload": candidate,
                            "duration_ms": 90,
                            "evidence_kind": "provider_observation",
                        }
                    ]
                },
            },
        )
    }

    assert results["baseline"].evidence_kind == "provider_observation"
    assert results["baseline"].identity_precision == 1.0
    assert results["baseline"].identity_recall == 1.0
    assert results["baseline"].portion_mae_g == 0
    assert results["baseline"].calories_mae_kcal == 4
    assert results["baseline"].repeatability_calorie_sd_kcal > 0
    assert results["baseline"].latency_p50_ms == 110
    assert results["baseline"].input_tokens is None
    assert results["candidate"].portion_mae_g == pytest.approx(45)


def test_micro_units_unknown_missing_and_masked_states_are_distinct():
    assert MICRO_UNITS == {
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
    micros = dict.fromkeys(MICRO_UNITS)
    micros.update({"vitamin_a": 0, "sodium": 20})
    case = PromptEvalCase(
        case_id="reviewed-micro-states",
        expected_foods=({"aliases": ["food"], "quantity_g": 100},),
        expected_is_food=True,
        expected_micros={
            "vitamin_a": {"status": "known", "unit": "mcg", "value": [0, 2]},
            "iron": {"status": "known", "unit": "mg", "value": 1},
            "sodium": {"status": "unknown", "unit": "mg"},
            "vitamin_c": {"status": "masked", "unit": "mg"},
        },
    )
    payload = {
        "structured_data": {
            "foods": [
                {
                    "name": "Food",
                    "quantity_g": 100,
                    "macros": {"protein_g": 1, "carbs_g": 2, "fat_g": 1},
                    "micros": micros,
                }
            ]
        }
    }
    result = PromptEvalLoop().rank_candidates(
        {"candidate": "prompt"},
        [case],
        case_overrides={
            "candidate": {
                case.case_id: {
                    "response_payload": payload,
                    "evidence_kind": "provider_observation",
                }
            }
        },
    )[0]

    assert result.food_detection_accuracy == 1
    assert result.micro_coverage == {"vitamin_a": 1.0, "iron": 0.0}
    assert result.micro_normalized_mae == {"vitamin_a": 0.0, "iron": 1.0}
    assert result.unknown_micro_population == {"sodium": 1}


def test_fixture_observations_remain_contract_only_even_with_truth_fields():
    case = PromptEvalCase(
        case_id="fixture-only",
        response_payload=_valid_payload(),
        expected_foods=({"aliases": ["chicken"], "quantity_g": 120},),
        expected_calories_kcal=(100, 200),
    )
    result = PromptEvalLoop().rank_candidates(
        {"candidate": "prompt"},
        [case],
        case_overrides={
            "candidate": {
                case.case_id: {
                    "response_payload": _valid_payload(),
                    "duration_ms": 42,
                }
            }
        },
    )[0]

    assert result.evidence_kind == "offline_contract_only"
    assert result.identity_precision is None
    assert result.calories_mae_kcal is None
    assert result.latency_p50_ms is None


def test_mixed_provider_and_contract_observations_do_not_publish_quality_metrics():
    case = PromptEvalCase(
        case_id="mixed-evidence",
        expected_foods=({"aliases": ["chicken"], "quantity_g": 120},),
        expected_calories_kcal=(100, 200),
    )
    result = PromptEvalLoop().rank_candidates(
        {"candidate": "prompt"},
        [case],
        case_overrides={
            "candidate": {
                case.case_id: [
                    {
                        "response_payload": _valid_payload(),
                        "duration_ms": 20,
                        "evidence_kind": "provider_observation",
                    },
                    {"response_payload": _valid_payload(), "duration_ms": 30},
                ]
            }
        },
    )[0]

    assert result.evidence_kind == "offline_contract_only"
    assert result.identity_precision is None
    assert result.calories_mae_kcal is None
    assert result.micro_normalized_mae == {}
    assert result.latency_p50_ms is None

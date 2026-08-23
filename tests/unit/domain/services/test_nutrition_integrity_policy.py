import math

import pytest

from src.domain.services.nutrition_integrity_policy import (
    NUTRITION_INTEGRITY_POLICY_VERSION,
    NUTRITION_INTEGRITY_V1_FIXTURE_MATRIX,
    NutritionIntegrityPolicy,
    normalize_logical_origin,
    normalize_serving_options,
)


def _valid_payload() -> dict:
    return {
        "protein_100g": 2.7,
        "carbs_100g": 28.0,
        "fat_100g": 0.3,
        "fiber_100g": 0.4,
        "sugar_100g": 0.1,
        "calories_100g": 123.1,
        "metric_serving_amount": 100.0,
        "allowed_units": [{"unit": "g", "gram_weight": 1.0}],
    }


@pytest.fixture()
def policy() -> NutritionIntegrityPolicy:
    return NutritionIntegrityPolicy()


@pytest.mark.parametrize(
    ("_name", "changes", "accepted", "reason"), NUTRITION_INTEGRITY_V1_FIXTURE_MATRIX
)
def test_v1_fixture_matrix_covers_boundary_contract(
    policy, _name, changes, accepted, reason
):
    payload = _valid_payload()
    payload.update(changes)

    result = policy.evaluate(payload, require_metric_basis=True)

    assert result.accepted is accepted
    assert result.reason_code == reason
    assert result.policy_version == NUTRITION_INTEGRITY_POLICY_VERSION


@pytest.mark.parametrize(
    "payload",
    [
        {"protein_100g": 0, "carbs_100g": 0, "fat_100g": 0, "calories_100g": 0},
        {"protein_100g": 100, "carbs_100g": 0, "fat_100g": 0, "calories_100g": 400},
        {"protein_100g": 40, "carbs_100g": 40, "fat_100g": 30, "calories_100g": 670},
        {"protein_100g": 0, "carbs_100g": 0, "fat_100g": 100, "calories_100g": 900},
    ],
)
def test_v1_inclusive_boundaries_are_accepted(policy, payload):
    result = policy.evaluate(payload, require_metric_basis=False)

    assert result.accepted is True
    assert result.reason_code == "accepted"


def test_energy_tolerance_is_inclusive(policy):
    payload = {
        "protein_100g": 1,
        "carbs_100g": 0,
        "fat_100g": 0,
        "calories_100g": 24.0,
    }

    assert policy.evaluate(payload).accepted is True

    payload["calories_100g"] = 24.0001
    assert policy.evaluate(payload).reason_code == "energy_mismatch"


def test_fiber_and_sugar_have_independent_semantics(policy):
    payload = _valid_payload()
    payload.update(
        {
            "protein_100g": 0,
            "carbs_100g": 1,
            "fat_100g": 0,
            "fiber_100g": 2,
            "sugar_100g": 10,
            "calories_100g": 4,
        }
    )

    assert policy.evaluate(payload).accepted is True


def test_provider_100g_is_a_labelled_serving_and_g_is_one_gram():
    normalized = normalize_serving_options(
        [{"unit": "g", "gram_weight": 100, "description": "100 g"}],
        provider_100g_label=True,
    )

    assert normalized == [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 100.0, "description": "100 g"},
    ]


def test_provider_gram_alias_is_labelled_serving_and_g_is_one_gram():
    normalized = normalize_serving_options(
        [{"unit": "gram", "gram_weight": 100, "description": "100 gram"}],
        provider_100g_label=True,
    )

    assert normalized == [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "serving", "gram_weight": 100.0, "description": "100 gram"},
    ]


@pytest.mark.parametrize(
    "serving",
    [
        {"unit": "g", "gram_weight": 100},
        {"unit": "cup", "gram_weight": 0},
        {"unit": "cup", "gram_weight": math.inf},
        {"unit": "cup", "gram_weight": 10001},
    ],
)
def test_canonical_serving_options_fail_closed(serving):
    assert normalize_serving_options([serving]) is None


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"food_reference_id": 7}, "local"),
        ({"fdc_id": 123}, "usda"),
        ({"source": "fatsecret", "food_id": "50953"}, "provider"),
        ({"custom": True}, "custom"),
    ],
)
def test_logical_origin_normalizes_exactly_one_source(fields, expected):
    result = normalize_logical_origin(fields)

    assert result.accepted is True
    assert result.origin == expected


def test_search_display_allows_missing_macros(policy):
    result = policy.evaluate(
        {"source": "fatsecret", "food_id": "1"},
        require_macros=False,
        origin_fields={"source": "fatsecret", "food_id": "1"},
    )

    assert result.accepted is True
    assert result.protein_100g is None
    assert result.serving_options


def test_logical_origin_rejects_multiple_sources():
    result = normalize_logical_origin({"food_reference_id": 7, "fdc_id": 123})

    assert result.accepted is False
    assert result.reason_code == "multiple_origins"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "name": "Verified generic potato",
            "protein_100g": 1.0,
            "carbs_100g": 1.0,
            "fat_100g": 99.9,
            "fiber_100g": 0.0,
            "sugar_100g": 0.0,
            "calories_100g": 900.0,
        },
        {
            "name": "Verified impossible row",
            "protein_100g": 100.0,
            "carbs_100g": 100.0,
            "fat_100g": 100.0,
            "fiber_100g": 0.0,
            "sugar_100g": 0.0,
            "calories_100g": 1500.0,
        },
    ],
)
def test_production_shaped_catastrophic_rows_fail_closed(policy, payload):
    result = policy.evaluate(payload, require_metric_basis=False)

    assert result.accepted is False
    assert result.reason_code in {
        "energy_out_of_range",
        "macro_mass_out_of_range",
    }

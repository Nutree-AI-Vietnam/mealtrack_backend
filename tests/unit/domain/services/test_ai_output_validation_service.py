import pytest

from src.domain.exceptions.ai_exceptions import AIOutputValidationError
from src.domain.model.ai.nutrition_contracts import (
    MealTextNutritionResponse,
    VisionNutritionResponse,
)
from src.domain.services.ai_output_validation_service import (
    build_parse_text_correction_prompt,
    build_validation_retry_prompt,
    validate_ai_output,
    validate_parse_text_nutrition,
)


def test_validate_ai_output_returns_contract_dump():
    payload = {
        "dish_name": "Rice bowl",
        "foods": [
            {
                "name": "rice",
                "quantity_g": 180,
                "macros": {"protein": 4, "carbs": 50, "fat": 1},
            }
        ],
    }

    result = validate_ai_output(
        payload,
        schema=VisionNutritionResponse,
        purpose="meal_scan",
        attempt_count=1,
    )

    assert result["foods"][0]["quantity_g"] == pytest.approx(180)
    assert "calories" not in result


def test_validate_ai_output_raises_sanitized_error_details():
    payload = {
        "foods": [
            {
                "name": "rice",
                "quantity_g": 150000,
                "macros": {"protein": 4, "carbs": 50, "fat": 1},
            }
        ],
    }

    with pytest.raises(AIOutputValidationError) as exc_info:
        validate_ai_output(
            payload,
            schema=VisionNutritionResponse,
            purpose="meal_scan",
            attempt_count=1,
        )

    assert exc_info.value.purpose == "meal_scan"
    assert exc_info.value.attempt_count == 1
    assert exc_info.value.validation_details
    assert "foods.0.quantity_g" in exc_info.value.validation_details[0]
    assert "150000" not in exc_info.value.validation_details[0]


def test_build_validation_retry_prompt_uses_compact_correction_context():
    error = AIOutputValidationError(
        "Invalid AI output",
        purpose="meal_scan",
        attempt_count=1,
        validation_details=["foods.0.quantity_g: Input should be <= 10000"],
    )

    prompt = build_validation_retry_prompt("Analyze this image.", error)

    assert prompt.startswith("Analyze this image.")
    assert "Return the full corrected response" in prompt
    assert "foods.0.quantity_g" in prompt


def test_parse_text_correction_includes_bounded_previous_candidate_as_data():
    error = AIOutputValidationError(
        "Invalid parse-text nutrition",
        purpose="parse_text",
        attempt_count=1,
        validation_details=["items.0.macros: exceed edible grams"],
    )
    candidate = {"items": [{"name": "Granola", "quantity": 10, "fat": 3.5}]}

    prompt = build_parse_text_correction_prompt("10g granula", candidate, error)

    assert "10g granula" in prompt
    assert '"fat":3.5' in prompt
    assert "untrusted data" in prompt
    assert "Preserve explicit amounts" in prompt


def _text_portion(**changes):
    item = {
        "name": "Granola",
        "quantity": 10,
        "unit": "g",
        "quantity_g": 10,
        "macros": {
            "protein_g": 1,
            "carbs_g": 6,
            "fat_g": 2,
            "fiber_g": 1,
            "sugar_g": 2,
        },
        **changes,
    }
    return validate_ai_output(
        {"items": [item]},
        schema=MealTextNutritionResponse,
        purpose="parse_text",
        attempt_count=1,
    )


@pytest.mark.parametrize(
    ("macros", "field"),
    [
        ({"protein_g": 1, "carbs_g": 8, "fat_g": 2}, "macros"),
        ({"protein_g": 1, "carbs_g": 6, "fat_g": 2, "fiber_g": 7}, "fiber_g"),
        ({"protein_g": 1, "carbs_g": 6, "fat_g": 2, "sugar_g": 7}, "sugar_g"),
    ],
    ids=["reported-11g-for-10g", "fiber-exceeds-carbs", "sugar-exceeds-carbs"],
)
def test_parse_text_rejects_impossible_portion_without_exposing_food(macros, field):
    payload = _text_portion(macros=macros)

    with pytest.raises(AIOutputValidationError) as error:
        validate_parse_text_nutrition(payload, attempt_count=2)

    assert error.value.purpose == "parse_text"
    assert error.value.attempt_count == 2
    assert any(field in detail for detail in error.value.validation_details)
    assert "Granola" not in str(error.value)


@pytest.mark.parametrize(
    ("micros", "field"),
    [
        ({"saturated_fat": 3}, "saturated_fat"),
        ({"added_sugar": 3}, "added_sugar"),
        ({"iron": float("inf")}, "iron"),
    ],
)
def test_parse_text_rejects_invalid_present_micros(micros, field):
    with pytest.raises(AIOutputValidationError) as error:
        validate_parse_text_nutrition(_text_portion(micros=micros))

    assert any(f"micros.{field}" in detail for detail in error.value.validation_details)


@pytest.mark.parametrize(
    ("quantity", "unit", "estimated_grams"),
    [(10, "g", 100), (0.01, "kg", 100), (10, "grams", 11)],
)
def test_stated_mass_cannot_be_replaced_by_larger_estimate(
    quantity, unit, estimated_grams
):
    with pytest.raises(AIOutputValidationError) as error:
        validate_parse_text_nutrition(
            _text_portion(quantity=quantity, unit=unit, quantity_g=estimated_grams)
        )

    assert any("quantity_g" in detail for detail in error.value.validation_details)


def test_parse_text_accepts_independent_macro_rounding_without_mutating_portion():
    payload = _text_portion(
        macros={"protein_g": 1.1, "carbs_g": 7.1, "fat_g": 1.9},
        micros={"vitamin_c": None, "sodium": 0, "saturated_fat": 1.9},
    )

    validate_parse_text_nutrition(payload)

    assert payload["items"][0]["macros"]["carbs_g"] == 7.1
    assert payload["items"][0]["micros"]["sodium"] == 0
    assert payload["items"][0]["micros"]["vitamin_c"] is None


def test_parse_text_volume_uses_estimated_edible_mass():
    payload = _text_portion(
        name="Olive oil",
        quantity=200,
        unit="ml",
        quantity_g=182,
        macros={"protein_g": 0, "carbs_g": 0, "fat_g": 182},
    )

    validate_parse_text_nutrition(payload)

    assert payload["items"][0]["quantity_g"] == 182


def test_parse_text_unknown_count_mass_still_checks_subsets():
    payload = _text_portion(quantity=1, unit="slice", quantity_g=None)
    validate_parse_text_nutrition(payload)
    payload["items"][0]["macros"]["fiber_g"] = 8

    with pytest.raises(AIOutputValidationError):
        validate_parse_text_nutrition(payload)

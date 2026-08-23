"""Cover handle_exception generic Exception branch (unexpected errors)."""

from fastapi import HTTPException, status

from src.api.exceptions import handle_exception
from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
    MealResponseLocalizationError,
)
from src.domain.services.nutrition_integrity_policy import (
    NutritionIntegrityError,
    NutritionIntegrityResult,
)


def test_handle_exception_unexpected_returns_500():
    exc = handle_exception(RuntimeError("boom"))
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc.detail["error_code"] == "INTERNAL_ERROR"
    assert exc.detail["message"] == "An unexpected error occurred"
    # Internal exception text must never reach the client; it is logged server-side only.
    assert exc.detail["details"] == {}
    assert "boom" not in str(exc.detail)


def test_handle_exception_ai_unavailable_returns_503_without_provider_error():
    exc = handle_exception(
        AIUnavailableError(
            "All models failed for discovery",
            attempted_models=["gpt-5.4-mini-2026-03-17", "gpt-5.4-mini-2026-03-17"],
            last_error="429 RESOURCE_EXHAUSTED",
        )
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc.detail["error_code"] == "AI_UNAVAILABLE"
    assert exc.detail["message"] == "AI meal generation is temporarily unavailable"
    assert exc.detail["details"] == {
        "attempted_models": ["gpt-5.4-mini-2026-03-17", "gpt-5.4-mini-2026-03-17"],
    }
    assert "RESOURCE_EXHAUSTED" not in str(exc.detail)


def test_handle_exception_ai_output_validation_returns_422_without_field_details():
    exc = handle_exception(
        AIOutputValidationError(
            "Invalid AI output after validation retry",
            purpose="meal_scan",
            attempt_count=2,
            validation_details=["foods.0.quantity_g: Input should be <= 10000"],
        )
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc.detail["error_code"] == "AI_OUTPUT_INVALID"
    assert "clearer photo" in exc.detail["message"]
    assert exc.detail["details"] == {
        "purpose": "meal_scan",
        "attempt_count": 2,
    }
    assert "quantity_g" not in str(exc.detail)


def test_handle_exception_localization_validation_returns_422_without_internal_details():
    exc = handle_exception(
        MealResponseLocalizationError("localized food item is missing")
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc.detail == {
        "error_code": "AI_OUTPUT_INVALID",
        "message": (
            "AI could not produce complete localized meal information. "
            "Please try again with a clearer photo."
        ),
        "details": {},
    }
    assert "missing" not in str(exc.detail)


def test_handle_exception_provider_failure_returns_503_without_internal_details():
    exc = handle_exception(
        NutritionIntegrityError(
            NutritionIntegrityResult(
                accepted=False,
                reason_code="provider_unavailable",
            )
        )
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert exc.detail == {
        "error_code": "NUTRITION_PROVIDER_UNAVAILABLE",
        "message": (
            "Nutrition provider capacity is temporarily unavailable. "
            "Please try again later."
        ),
        "details": {},
    }


def test_handle_exception_integrity_rejection_hides_internal_reason_code():
    exc = handle_exception(
        NutritionIntegrityError(
            NutritionIntegrityResult(
                accepted=False,
                reason_code="provider_identity_mismatch",
            )
        )
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc.detail == {
        "error_code": "NUTRITION_INTEGRITY_REJECTED",
        "message": "The nutrition data could not be verified.",
        "details": {},
    }

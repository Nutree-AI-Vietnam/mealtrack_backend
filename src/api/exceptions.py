"""
API Exception classes and conversion helpers for consistent error handling.

Log-or-raise rule: handle_exception() is a PURE CONVERSION HELPER.
It does not log expected exceptions (MealTrackException, HTTPException).
AIUnavailableError is logged at WARNING because it represents a degraded
service state worth operational visibility.
Unexpected exceptions log once at ERROR; the global exception handler in
exception_handlers.py owns that log after route try/except blocks are removed.
"""

import logging
from typing import Any

from fastapi import HTTPException, status

from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
    MealResponseLocalizationError,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError

logger = logging.getLogger(__name__)

_NUTRITION_PROVIDER_UNAVAILABLE_REASONS = frozenset(
    {
        "provider_budget_unavailable",
        "provider_resolution_unavailable",
        "provider_unavailable",
    }
)


class MealTrackException(Exception):
    """Base exception for all MealTrack exceptions."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)


class ValidationException(MealTrackException):
    """Raised when request validation fails."""

    pass


class ResourceNotFoundException(MealTrackException):
    """Raised when a requested resource is not found."""

    pass


class BusinessLogicException(MealTrackException):
    """Raised when business rules are violated."""

    pass


class ConflictException(MealTrackException):
    """Raised when a request conflicts with current resource state (e.g., cooldown)."""

    pass


class ExternalServiceException(MealTrackException):
    """Raised when an external service fails."""

    pass


class AuthenticationException(MealTrackException):
    """Raised when authentication fails."""

    pass


class AuthorizationException(MealTrackException):
    """Raised when user lacks permission."""

    pass


def create_http_exception(exc: MealTrackException) -> HTTPException:
    """Convert domain exception to HTTP exception with appropriate status code."""

    status_map = {
        ValidationException: status.HTTP_400_BAD_REQUEST,
        ResourceNotFoundException: status.HTTP_404_NOT_FOUND,
        BusinessLogicException: status.HTTP_422_UNPROCESSABLE_ENTITY,
        ExternalServiceException: status.HTTP_503_SERVICE_UNAVAILABLE,
        AuthenticationException: status.HTTP_401_UNAUTHORIZED,
        AuthorizationException: status.HTTP_403_FORBIDDEN,
        ConflictException: status.HTTP_409_CONFLICT,
    }

    status_code = status_map.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)

    return HTTPException(
        status_code=status_code,
        detail={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
        },
    )


def handle_exception(exc: Exception) -> HTTPException:
    """Convert any exception to an appropriate HTTPException.

    Expected exceptions are converted silently (no ERROR log).
    AIUnavailableError logs WARNING (degraded state worth noting).
    Unexpected exceptions log ERROR once — until all routes drop this
    helper in favour of direct propagation to the global handler
    (see exception_handlers.py and Phase 3 route cleanup).
    """

    if isinstance(exc, AIUnavailableError):
        # Degraded service state — WARNING is appropriate
        logger.warning(
            "AI provider unavailable: %s",
            type(exc).__name__,
            extra={
                "error_code": "AI_UNAVAILABLE",
                "attempted_models": exc.attempted_models,
            },
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "AI_UNAVAILABLE",
                "message": "AI meal generation is temporarily unavailable",
                "details": {"attempted_models": exc.attempted_models},
            },
        )

    if isinstance(exc, AIOutputValidationError):
        logger.warning(
            "AI output validation failed: %s",
            exc,
            extra={
                "error_code": "AI_OUTPUT_INVALID",
                "purpose": exc.purpose,
                "attempt_count": exc.attempt_count,
                "validation_details": exc.validation_details,
            },
        )
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "AI_OUTPUT_INVALID",
                "message": (
                    "AI could not produce valid nutrition analysis. "
                    "Please try a clearer photo or more specific meal description."
                ),
                "details": {
                    "purpose": exc.purpose,
                    "attempt_count": exc.attempt_count,
                },
            },
        )

    if isinstance(exc, MealResponseLocalizationError):
        logger.warning("AI localization output validation failed: %s", exc)
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "AI_OUTPUT_INVALID",
                "message": (
                    "AI could not produce complete localized meal information. "
                    "Please try again with a clearer photo."
                ),
                "details": {},
            },
        )

    if isinstance(exc, NutritionIntegrityError):
        result = exc.result
        if result.reason_code in _NUTRITION_PROVIDER_UNAVAILABLE_REASONS:
            return HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "NUTRITION_PROVIDER_UNAVAILABLE",
                    "message": (
                        "Nutrition provider capacity is temporarily unavailable. "
                        "Please try again later."
                    ),
                    "details": {},
                },
            )
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "NUTRITION_INTEGRITY_REJECTED",
                "message": "The nutrition data could not be verified.",
                "details": {},
            },
        )

    if isinstance(exc, MealTrackException):
        # Expected domain error — pure conversion, no log (it is not an error)
        return create_http_exception(exc)

    if isinstance(exc, HTTPException):
        # Already an HTTP exception — pass through
        return exc

    # Unexpected exceptions — one root-cause ERROR; no internal details to client
    logger.error(
        "Unexpected exception: %s",
        type(exc).__name__,
        exc_info=True,
        extra={"error_type": type(exc).__name__},
    )

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "error_code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
            "details": {},
        },
    )

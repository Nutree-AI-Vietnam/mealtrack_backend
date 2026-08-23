"""
Global FastAPI exception handlers — one authoritative boundary per exception class.

Rule: log-or-raise, not both.
- Expected domain exceptions: convert to response, no ERROR log.
- Unexpected exceptions: one ERROR with stack trace, one safe 500 response.

Sentry captures Python ERROR log records, so keeping this count to one
prevents duplicate Sentry issues per failure event.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.exceptions import (
    MealTrackException,
    create_http_exception,
    handle_exception,
)
from src.domain.exceptions.ai_exceptions import AIUnavailableError
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError

logger = logging.getLogger(__name__)


async def _meal_track_exception_handler(
    request: Request, exc: MealTrackException
) -> JSONResponse:
    """Convert expected domain exceptions to JSON without logging at ERROR.

    Wraps the detail in {"detail": ...} to match the existing HTTPException
    response contract that clients depend on.
    """
    http_exc = create_http_exception(exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content={"detail": http_exc.detail},
    )


async def _ai_unavailable_handler(
    request: Request, exc: AIUnavailableError
) -> JSONResponse:
    """Log degraded AI state at WARNING; return 503 without ERROR."""
    logger.warning(
        "AI provider unavailable: %s",
        type(exc).__name__,
        extra={
            "error_code": "AI_UNAVAILABLE",
            "attempted_models": exc.attempted_models,
        },
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "error_code": "AI_UNAVAILABLE",
                "message": "AI meal generation is temporarily unavailable",
                "details": {"attempted_models": exc.attempted_models},
            }
        },
    )


async def _nutrition_exception_handler(
    request: Request,
    exc: NutritionIntegrityError,
) -> JSONResponse:
    """Convert nutrition trust-boundary failures at the global API boundary."""
    http_exc = handle_exception(exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content={"detail": http_exc.detail},
        headers=http_exc.headers,
    )


async def _unexpected_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """
    Catch-all for unexpected exceptions — the single root-cause ERROR owner.

    Logs once with full stack trace and safe request context.
    No raw body, query payload, auth header, or internal exception text
    is sent to the client.
    """
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    logger.error(
        "Unexpected error handling %s %s: %s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=True,
        extra={
            "error_type": type(exc).__name__,
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "error_code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on *app*.

    MealTrackException and AIUnavailableError route to ExceptionMiddleware
    (Starlette MRO lookup handles subclasses automatically). Exception routes
    to ServerErrorMiddleware.handler, which always re-raises after calling
    the handler — so RequestLoggerMiddleware._log_error fires at WARNING to
    avoid a duplicate root-cause ERROR in the middleware escape path.
    """
    # MealTrackException and subclasses — expected, no ERROR
    app.add_exception_handler(MealTrackException, _meal_track_exception_handler)  # type: ignore[arg-type]

    # AIUnavailableError — degraded, WARNING only
    app.add_exception_handler(AIUnavailableError, _ai_unavailable_handler)  # type: ignore[arg-type]

    # Nutrition trust-boundary failures are expected request/provider outcomes.
    app.add_exception_handler(
        NutritionIntegrityError,
        _nutrition_exception_handler,  # type: ignore[arg-type]
    )
    # Catch-all for truly unexpected exceptions — one ERROR (ServerErrorMiddleware)
    app.add_exception_handler(Exception, _unexpected_exception_handler)

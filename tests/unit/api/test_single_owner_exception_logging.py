"""
Regression guards for the single-owner logger policy.

Rule: one authoritative root-cause ERROR per unexpected request failure.
Expected 4xx/business exceptions must NOT produce ERROR logs.
Background/cron failures have their own ERROR boundary.

These tests describe intended post-refactor behavior. Some will FAIL
before Phase 2 (central exception boundary) is in place.
"""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.exception_handlers import register_exception_handlers
from src.api.exceptions import (
    BusinessLogicException,
    ResourceNotFoundException,
    ValidationException,
    handle_exception,
)
from src.api.middleware.request_logger import RequestLoggerMiddleware
from src.domain.exceptions.ai_exceptions import AIUnavailableError
from src.domain.services.nutrition_integrity_policy import (
    NutritionIntegrityError,
    NutritionIntegrityResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Minimal FastAPI app matching production config: middleware + global exception handlers."""
    app = FastAPI()
    app.add_middleware(RequestLoggerMiddleware)
    register_exception_handlers(app)
    return app


def _error_records(caplog) -> list:
    return [r for r in caplog.records if r.levelname == "ERROR"]


# ---------------------------------------------------------------------------
# Unexpected exception path — WILL FAIL before Phase 2 fix
# ---------------------------------------------------------------------------


class TestUnexpectedExceptionOwnership:
    """Unexpected exceptions must produce exactly one root-cause ERROR."""

    def test_unexpected_exception_produces_one_error(self, caplog):
        """
        A route raising an unhandled RuntimeError should emit exactly one
        root-cause ERROR. Currently FAILS because handle_exception logs ERROR
        AND _log_response(status=500) also logs at ERROR level.
        """
        app = _make_app()

        @app.get("/boom")
        def boom():
            raise RuntimeError("unexpected failure")

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR):
            response = client.get("/boom")

        assert response.status_code == 500
        errors = _error_records(caplog)
        assert len(errors) == 1, (
            f"Expected 1 root-cause ERROR, got {len(errors)}: "
            + "; ".join(r.message for r in errors)
        )

    def test_handle_exception_pattern_produces_one_error(self, caplog):
        """
        Current route pattern: raise handle_exception(e). After Phase 2
        fix, handle_exception must NOT log for unexpected exceptions —
        the central handler owns that single ERROR.
        """
        app = _make_app()

        @app.get("/boom-legacy")
        def boom_legacy():
            try:
                raise RuntimeError("unexpected in legacy route")
            except Exception as e:
                raise handle_exception(e) from e

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR):
            response = client.get("/boom-legacy")

        assert response.status_code == 500
        errors = _error_records(caplog)
        assert len(errors) == 1, (
            f"Expected 1 root-cause ERROR from handle_exception pattern, got {len(errors)}: "
            + "; ".join(r.message for r in errors)
        )

    def test_unexpected_error_response_body_hides_details(self, caplog):
        """Client must never receive internal exception text in 500 response."""
        app = _make_app()

        @app.get("/leaky")
        def leaky():
            raise RuntimeError("db_password=secret123")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/leaky")

        assert response.status_code == 500
        body = response.text
        assert "db_password" not in body
        assert "secret123" not in body


# ---------------------------------------------------------------------------
# Expected exception paths — must produce ZERO ERROR logs
# ---------------------------------------------------------------------------


class TestExpectedExceptionOwnership:
    """Expected 4xx/business exceptions must NOT produce ERROR logs."""

    @pytest.mark.parametrize(
        "status_code",
        [400, 401, 403, 404, 409, 422],
    )
    def test_expected_http_exception_produces_no_error(self, caplog, status_code):
        app = _make_app()

        @app.get("/expected")
        def expected():
            raise HTTPException(status_code=status_code, detail="expected error")

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR):
            response = client.get("/expected")

        assert response.status_code == status_code
        errors = _error_records(caplog)
        assert errors == [], (
            f"HTTPException({status_code}) must not produce ERROR; got: "
            + "; ".join(r.message for r in errors)
        )

    @pytest.mark.parametrize(
        "exc_class",
        [
            ValidationException,
            ResourceNotFoundException,
            BusinessLogicException,
        ],
    )
    def test_meal_track_exception_produces_no_error(self, caplog, exc_class):
        """Domain exceptions translated through handle_exception must not ERROR."""
        app = _make_app()

        @app.get("/domain-exc")
        def domain_exc():
            try:
                raise exc_class("business rule violation")
            except Exception as e:
                raise handle_exception(e) from e

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR):
            response = client.get("/domain-exc")

        # Should be 4xx
        assert response.status_code < 500
        errors = _error_records(caplog)
        assert errors == [], (
            f"{exc_class.__name__} must not produce ERROR; got: "
            + "; ".join(r.message for r in errors)
        )

    def test_ai_unavailable_produces_no_error(self, caplog):
        """AIUnavailableError → 503 should produce at most a WARNING, not ERROR."""
        app = _make_app()

        @app.get("/ai-unavailable")
        def ai_unavailable():
            try:
                raise AIUnavailableError(
                    "All models failed",
                    attempted_models=["gpt-5.4-mini-2026-03-17"],
                    last_error="429",
                )
            except Exception as e:
                raise handle_exception(e) from e

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR):
            response = client.get("/ai-unavailable")

        assert response.status_code == 503
        errors = _error_records(caplog)
        assert errors == [], (
            "AIUnavailableError must not produce ERROR; got: "
            + "; ".join(r.message for r in errors)
        )

    def test_nutrition_provider_failure_is_global_503_without_error(self, caplog):
        app = _make_app()

        @app.get("/nutrition-provider-failure")
        def nutrition_provider_failure():
            raise NutritionIntegrityError(
                NutritionIntegrityResult(
                    accepted=False,
                    reason_code="provider_unavailable",
                )
            )

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.ERROR):
            response = client.get("/nutrition-provider-failure")

        assert response.status_code == 503
        assert response.json()["detail"]["error_code"] == (
            "NUTRITION_PROVIDER_UNAVAILABLE"
        )
        assert _error_records(caplog) == []


# ---------------------------------------------------------------------------
# Middleware outcome log — must never duplicate root-cause ERROR
# ---------------------------------------------------------------------------


class TestMiddlewareOutcomeLog:
    """Middleware logs request outcome; it must not duplicate root-cause ERROR."""

    def test_500_outcome_log_is_not_additional_error(self, caplog):
        """
        After the central boundary fix, the middleware [RES-...] log for a 500
        must be WARNING or INFO — not an additional ERROR that duplicates the
        central handler's root-cause ERROR.
        """
        app = _make_app()

        @app.get("/fails")
        def fails():
            raise RuntimeError("oops")

        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level(logging.DEBUG):
            client.get("/fails")

        res_logs = [r for r in caplog.records if "[RES-" in r.message]
        error_res_logs = [r for r in res_logs if r.levelno >= logging.ERROR]
        assert error_res_logs == [], (
            "Middleware [RES-...] log for 500 must not be at ERROR level after fix; "
            "use WARNING so root-cause ERROR stays unique"
        )



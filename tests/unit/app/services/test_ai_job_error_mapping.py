"""What an AI job attempt raised, as the error code the app shows."""

import asyncio
import logging
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from src.api.exceptions import (
    ConflictException,
    ValidationException,
    handle_exception,
)
from src.app.services.ai_jobs.error_mapping import (
    INTERNAL_ERROR,
    ai_job_error_code,
    map_job_exception,
)
from src.domain.exceptions.ai_exceptions import (
    AIContentRejectedError,
    AIOutputValidationError,
    AIUnavailableError,
    AIVisionError,
    AIVisionFailureKind,
    MealResponseLocalizationError,
)
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
)
from src.domain.model.ai_jobs import AiJobError, AiJobErrorKind
from src.domain.parsers.vision_response_parser import VisionResponseParsingError
from src.domain.services.nutrition_calculation_service import (
    AuthoritativeUnitMismatchError,
)
from src.domain.services.nutrition_integrity_policy import (
    NutritionIntegrityError,
    NutritionIntegrityResult,
)

PERMANENT = AiJobErrorKind.PERMANENT
TRANSIENT = AiJobErrorKind.TRANSIENT


def _integrity_error(reason_code: str) -> NutritionIntegrityError:
    return NutritionIntegrityError(
        NutritionIntegrityResult(accepted=False, reason_code=reason_code)
    )


def _error(code: str, kind: AiJobErrorKind, retryable: bool) -> AiJobError:
    return AiJobError(code=code, kind=kind, retryable=retryable)


# --- errors the job layer names itself -------------------------------------


@pytest.mark.parametrize(
    "code", ["CHAT_TURN_FAILED", "FAILED_TO_ANALYZE_MEAL_IMAGE", "INVALID_JOB_INPUT"]
)
def test_executor_errors_carry_their_own_code(code):
    assert ai_job_error_code(AiJobExecutionError(code, "detail for logs")) == code


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        pytest.param(ChatBusyError(), "CHAT_BUSY", id="busy"),
        pytest.param(ChatRateLimitedError(30), "CHAT_RATE_LIMITED", id="rate-limited"),
        pytest.param(
            ChatRateLimitedError(3600, daily=True), "CHAT_DAILY_LIMIT", id="daily"
        ),
        pytest.param(
            ChatProviderUnavailableError(), "CHAT_PROVIDER_UNAVAILABLE", id="provider"
        ),
        pytest.param(
            ChatProviderUnavailableError(retryable=False),
            "CHAT_PROVIDER_UNAVAILABLE",
            id="provider-not-retryable",
        ),
        pytest.param(
            ChatIdempotencyConflictError(), "INVALID_JOB_INPUT", id="idempotency"
        ),
    ],
)
def test_chat_errors(exc, code):
    assert ai_job_error_code(exc) == code


_VISION_CODES = {
    AIVisionFailureKind.transient: "AI_UNAVAILABLE",
    AIVisionFailureKind.timeout: "AI_TIMEOUT",
    AIVisionFailureKind.rate_limit: "AI_UNAVAILABLE",
    AIVisionFailureKind.schema_validation: "AI_OUTPUT_INVALID",
    AIVisionFailureKind.json_parse: "AI_OUTPUT_INVALID",
    AIVisionFailureKind.no_food: "NOT_FOOD_IMAGE",
    AIVisionFailureKind.unknown: "AI_UNAVAILABLE",
}


@pytest.mark.parametrize("kind", list(AIVisionFailureKind), ids=lambda k: k.value)
def test_vision_failures_map_by_kind(kind):
    error = AIVisionError("vision failed", kind, provider="gemini", model="m1")

    assert ai_job_error_code(error) == _VISION_CODES[kind]


def test_unparseable_vision_output_is_invalid_ai_output():
    error = VisionResponseParsingError("no JSON object")

    assert ai_job_error_code(error) == "AI_OUTPUT_INVALID"


def test_timeouts_keep_their_own_code_unlike_the_synchronous_endpoints():
    assert ai_job_error_code(TimeoutError()) == "AI_TIMEOUT"
    assert handle_exception(TimeoutError()).detail["error_code"] == "AI_UNAVAILABLE"


@pytest.mark.asyncio
async def test_an_attempt_cut_off_by_wait_for_is_a_timeout():
    with pytest.raises(TimeoutError) as excinfo:
        await asyncio.wait_for(asyncio.sleep(1), timeout=0.001)

    assert ai_job_error_code(excinfo.value) == "AI_TIMEOUT"


@pytest.mark.parametrize(
    "exc",
    [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(1), GeneratorExit()],
    ids=["cancelled", "keyboard-interrupt", "system-exit", "generator-exit"],
)
def test_interruptions_are_internal_errors_without_the_http_mapper(exc):
    with patch("src.app.services.ai_jobs.error_mapping.handle_exception") as handle:
        assert ai_job_error_code(exc) == INTERNAL_ERROR

    handle.assert_not_called()


# --- everything else: the synchronous endpoints' codes ---------------------


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        pytest.param(
            AIUnavailableError("all models down", attempted_models=["m1"]),
            "AI_UNAVAILABLE",
            id="ai-unavailable",
        ),
        pytest.param(
            AIContentRejectedError("blocked", provider="openai", model="m1"),
            "AI_CONTENT_REJECTED",
            id="content-rejected",
        ),
        pytest.param(
            AIOutputValidationError("bad", purpose="parse_text", attempt_count=2),
            "AI_OUTPUT_INVALID",
            id="output-invalid",
        ),
        pytest.param(
            MealResponseLocalizationError("missing name_vi"),
            "AI_OUTPUT_INVALID",
            id="localization",
        ),
        pytest.param(
            AuthoritativeUnitMismatchError("cup"),
            "NUTRITION_UNIT_INVALID",
            id="unit-mismatch",
        ),
        pytest.param(
            _integrity_error("provider_budget_unavailable"),
            "NUTRITION_PROVIDER_UNAVAILABLE",
            id="provider-budget",
        ),
        pytest.param(
            _integrity_error("provider_resolution_unavailable"),
            "NUTRITION_PROVIDER_UNAVAILABLE",
            id="provider-resolution",
        ),
        pytest.param(
            _integrity_error("provider_unavailable"),
            "NUTRITION_PROVIDER_UNAVAILABLE",
            id="provider",
        ),
        pytest.param(
            _integrity_error("invalid_serving"),
            "NUTRITION_INTEGRITY_REJECTED",
            id="integrity-rejected",
        ),
        pytest.param(
            ValidationException("not food", error_code="NOT_FOOD_IMAGE"),
            "NOT_FOOD_IMAGE",
            id="not-food",
        ),
        pytest.param(
            ValidationException("no label", error_code="NOT_FOOD_LABEL_IMAGE"),
            "NOT_FOOD_LABEL_IMAGE",
            id="not-food-label",
        ),
        pytest.param(
            ValidationException("empty", error_code="INVALID_MEAL_TEXT"),
            "INVALID_MEAL_TEXT",
            id="invalid-text",
        ),
        pytest.param(
            ConflictException("no code given"),
            "ConflictException",
            id="code-defaults-to-class-name",
        ),
        pytest.param(ConnectionError("reset"), "AI_UNAVAILABLE", id="connection"),
        pytest.param(ConnectionResetError(), "AI_UNAVAILABLE", id="connection-reset"),
        pytest.param(BrokenPipeError(), "AI_UNAVAILABLE", id="broken-pipe"),
        pytest.param(
            httpx.ConnectError("refused"), "AI_UNAVAILABLE", id="httpx-connect"
        ),
        pytest.param(httpx.ReadTimeout("slow"), "AI_UNAVAILABLE", id="httpx-timeout"),
        pytest.param(
            httpx.RemoteProtocolError("eof"), "AI_UNAVAILABLE", id="httpx-protocol"
        ),
        pytest.param(
            HTTPException(status_code=400, detail={"error_code": "X_CODE"}),
            "X_CODE",
            id="http-exception",
        ),
        pytest.param(RuntimeError("boom"), INTERNAL_ERROR, id="runtime-error"),
        pytest.param(ValueError("bad"), INTERNAL_ERROR, id="value-error"),
    ],
)
def test_other_errors_get_the_code_the_synchronous_endpoints_return(exc, code):
    assert ai_job_error_code(exc) == code
    assert handle_exception(exc).detail["error_code"] == code


@pytest.mark.parametrize(
    "detail",
    ["Bad request", {"error_code": 42}, {"message": "no code"}, None],
    ids=["text", "non-text-code", "no-code", "default"],
)
def test_http_errors_without_a_text_code_are_internal(detail):
    error = HTTPException(status_code=400, detail=detail)

    assert ai_job_error_code(error) == INTERNAL_ERROR


def test_unexpected_errors_are_logged_once_with_their_traceback(caplog):
    raised = RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        try:
            raise raised
        except RuntimeError as exc:
            code = ai_job_error_code(exc)

    assert code == INTERNAL_ERROR
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "Unexpected exception: RuntimeError"
    assert record.exc_info[1] is raised


@pytest.mark.parametrize(
    "exc",
    [
        asyncio.CancelledError(),
        TimeoutError(),
        AIVisionError("no food", AIVisionFailureKind.no_food),
        ValidationException("not food", error_code="NOT_FOOD_IMAGE"),
    ],
    ids=["cancelled", "timeout", "vision", "validation"],
)
def test_expected_errors_are_mapped_without_logging(caplog, exc):
    with caplog.at_level(logging.WARNING):
        ai_job_error_code(exc)

    assert not caplog.records


# --- map_job_exception -----------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(
            ValidationException("not food", error_code="NOT_FOOD_IMAGE"),
            _error("NOT_FOOD_IMAGE", PERMANENT, False),
            id="not-food",
        ),
        pytest.param(
            AIContentRejectedError("blocked"),
            _error("AI_CONTENT_REJECTED", PERMANENT, False),
            id="content-rejected",
        ),
        pytest.param(
            ChatRateLimitedError(3600, daily=True),
            _error("CHAT_DAILY_LIMIT", PERMANENT, False),
            id="daily-limit",
        ),
        pytest.param(
            ChatIdempotencyConflictError(),
            _error("INVALID_JOB_INPUT", PERMANENT, False),
            id="idempotency-conflict",
        ),
        pytest.param(
            AIVisionError("bad json", AIVisionFailureKind.json_parse),
            _error("AI_OUTPUT_INVALID", PERMANENT, True),
            id="bad-ai-output",
        ),
        pytest.param(
            _integrity_error("invalid_serving"),
            _error("NUTRITION_INTEGRITY_REJECTED", PERMANENT, True),
            id="integrity-rejected",
        ),
        pytest.param(
            ChatRateLimitedError(30),
            _error("CHAT_RATE_LIMITED", TRANSIENT, True),
            id="rate-limited",
        ),
        pytest.param(
            TimeoutError(), _error("AI_TIMEOUT", TRANSIENT, True), id="timeout"
        ),
        pytest.param(
            _integrity_error("provider_unavailable"),
            _error("NUTRITION_PROVIDER_UNAVAILABLE", TRANSIENT, True),
            id="provider-unavailable",
        ),
        pytest.param(
            RuntimeError("boom"),
            _error(INTERNAL_ERROR, TRANSIENT, True),
            id="unexpected",
        ),
        pytest.param(
            asyncio.CancelledError(),
            _error(INTERNAL_ERROR, TRANSIENT, True),
            id="cancelled",
        ),
    ],
)
def test_map_job_exception_classifies_the_code(exc, expected):
    assert map_job_exception(exc) == expected

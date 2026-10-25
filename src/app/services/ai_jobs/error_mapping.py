"""Turn whatever an AI job attempt raised into a client-facing error code.

Codes match the ones the synchronous endpoints return, so the app shows the
same failure copy whichever way the request ran. Two differ on purpose: a
timeout is ``AI_TIMEOUT`` (sync says ``AI_UNAVAILABLE``) so the app can say
the request took too long, and a chat idempotency conflict is
``INVALID_JOB_INPUT`` (sync says ``CHAT_IDEMPOTENCY_CONFLICT``) because the
job's own input is at fault and retrying cannot fix it.
"""

from __future__ import annotations

from src.api.exceptions import handle_exception
from src.domain.exceptions.ai_exceptions import AIVisionError, AIVisionFailureKind
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
)
from src.domain.model.ai_jobs import AiJobError
from src.domain.model.ai_jobs.policies import classify_ai_job_error
from src.domain.parsers.vision_response_parser import VisionResponseParsingError

INTERNAL_ERROR = "INTERNAL_ERROR"

_VISION_CODES = {
    AIVisionFailureKind.no_food: "NOT_FOOD_IMAGE",
    AIVisionFailureKind.schema_validation: "AI_OUTPUT_INVALID",
    AIVisionFailureKind.json_parse: "AI_OUTPUT_INVALID",
    AIVisionFailureKind.timeout: "AI_TIMEOUT",
}


def ai_job_error_code(exc: BaseException) -> str:
    if isinstance(exc, AiJobExecutionError):
        return exc.code
    if isinstance(exc, ChatBusyError):
        return "CHAT_BUSY"
    if isinstance(exc, ChatRateLimitedError):
        return "CHAT_DAILY_LIMIT" if exc.daily else "CHAT_RATE_LIMITED"
    if isinstance(exc, ChatProviderUnavailableError):
        return "CHAT_PROVIDER_UNAVAILABLE"
    if isinstance(exc, ChatIdempotencyConflictError):
        return "INVALID_JOB_INPUT"
    if isinstance(exc, AIVisionError):
        return _VISION_CODES.get(exc.kind, "AI_UNAVAILABLE")
    if isinstance(exc, VisionResponseParsingError):
        return "AI_OUTPUT_INVALID"
    # handle_exception reports timeouts as AI_UNAVAILABLE; jobs keep them apart.
    if isinstance(exc, TimeoutError):
        return "AI_TIMEOUT"
    if not isinstance(exc, Exception):
        return INTERNAL_ERROR
    detail = handle_exception(exc).detail
    if isinstance(detail, dict) and isinstance(detail.get("error_code"), str):
        return str(detail["error_code"])
    return INTERNAL_ERROR


def map_job_exception(exc: BaseException) -> AiJobError:
    return classify_ai_job_error(ai_job_error_code(exc))

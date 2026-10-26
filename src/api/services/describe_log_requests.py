"""Request flow shared by POST /v1/hydration/describe and /v1/movement/describe.

The rate limiter has already run in the route decorator. Then: the guard
(switch, Premium, daily quota, sentence), then Idempotency-Key exact replay,
then the command. A command that fails before saving drops its claim so the
app can retry; a stored reply is replayed as it was first sent.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from src.api.exceptions import (
    ConflictException,
    MealTrackException,
    ValidationException,
    handle_exception,
)
from src.app.services.describe_log_guard import DescribeLogGuard
from src.bootstrap.describe_log import (
    DurableWriteConflictError,
    DurableWriteInProgressError,
    abandon_durable_write,
    begin_durable_write,
    canonicalize_fingerprint,
    complete_durable_write,
    normalize_idempotency_key,
)
from src.domain.exceptions.ai_exceptions import AIError

logger = logging.getLogger(__name__)


async def run_describe[ResponseT: BaseModel](
    *,
    user_id: str,
    text: str,
    timezone: str | None,
    idempotency_key: str | None,
    action: str,
    fingerprint: dict[str, Any],
    guard: DescribeLogGuard,
    send: Callable[[str], Awaitable[dict]],
    response_model: type[ResponseT],
) -> ResponseT:
    key = _require_idempotency_key(idempotency_key)
    sentence = await guard.check(user_id=user_id, text=text, timezone=timezone)
    request_fingerprint = canonicalize_fingerprint({"text": text, **fingerprint})
    claim = {
        "user_id": user_id,
        "action": action,
        "idempotency_key": key,
        "request_fingerprint": request_fingerprint,
    }
    try:
        existing = await begin_durable_write(**claim)
    except DurableWriteConflictError as exc:
        raise ConflictException(
            "Idempotency-Key reused with a different payload",
            "IDEMPOTENCY_KEY_CONFLICT",
        ) from exc
    except DurableWriteInProgressError as exc:
        raise ConflictException(
            "Idempotency-Key is already being processed",
            "IDEMPOTENCY_KEY_IN_PROGRESS",
        ) from exc
    if existing is not None:
        return response_model.model_validate(existing.response_body)

    try:
        result = await send(sentence)
    except (MealTrackException, AIError) as exc:
        # Raised before the unit of work, so nothing was saved: free the key
        # for a retry. handle_exception maps AI errors (422 invalid, 503 down).
        await abandon_durable_write(**claim)
        raise handle_exception(exc) from exc
    except Exception as exc:
        # The save may have committed. Keep the claim so a retry with this
        # key gets 409 instead of possibly saving the sentence twice.
        raise handle_exception(exc) from exc

    response = response_model.model_validate(result)
    try:
        stored = await complete_durable_write(
            **claim,
            response_status_code=200,
            response_body=response.model_dump(mode="json"),
        )
    except Exception:
        # The entries are saved. The claim stays pending, so a retry with this
        # key gets 409 instead of saving the sentence twice.
        logger.warning("Describe reply could not be stored for replay", exc_info=True)
        return response
    return response_model.model_validate(stored.response_body)


def _require_idempotency_key(raw: str | None) -> str:
    try:
        key = normalize_idempotency_key(raw)
    except ValueError as exc:
        raise ValidationException(str(exc), "IDEMPOTENCY_KEY_INVALID") from exc
    if key is None:
        raise ValidationException(
            "Idempotency-Key header is required", "IDEMPOTENCY_KEY_INVALID"
        )
    return key

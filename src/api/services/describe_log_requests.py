"""Request flow shared by POST /v1/hydration/describe and /v1/movement/describe.

The rate limiter has already run in the route decorator. A key whose sentence
was already logged replays its stored reply first, before the switch, Premium
and the daily quota, so a resend never spends quota or meets the paywall.
Then the guard (switch, Premium, daily quota, sentence) and the command, which
claims the key and stores its reply in the same transaction as the entries.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from src.api.exceptions import ValidationException, handle_exception
from src.app.services.describe_log_guard import DescribeLogGuard
from src.app.services.describe_log_replay import DescribeKey, stored_reply
from src.domain.utils.fingerprint_utils import (
    canonicalize_fingerprint,
    normalize_idempotency_key,
)


async def run_describe[ResponseT: BaseModel](
    *,
    user_id: str,
    text: str,
    timezone: str | None,
    idempotency_key: str | None,
    operation: str,
    fingerprint: dict[str, Any],
    guard: DescribeLogGuard,
    replay_uow_factory: Any,
    send: Callable[[str, DescribeKey], Awaitable[dict]],
    response_model: type[ResponseT],
) -> ResponseT:
    key = DescribeKey(
        operation,
        _require_idempotency_key(idempotency_key),
        canonicalize_fingerprint({"text": text, **fingerprint}),
    )
    reply = await stored_reply(replay_uow_factory, user_id, key)
    if reply is not None:
        return response_model.model_validate(reply)

    sentence = await guard.check(user_id=user_id, text=text, timezone=timezone)
    try:
        result = await send(sentence, key)
    except Exception as exc:
        # handle_exception maps AI errors too: an invalid reply is 422, an outage 503.
        raise handle_exception(exc) from exc
    return response_model.model_validate(result)


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

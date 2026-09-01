"""Authenticated single-thread Nutree coach API."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.chat import get_chat_turn_orchestrator
from src.api.exceptions import ValidationException
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.chat_requests import ChatMessageCreateRequest
from src.api.schemas.response.chat_responses import (
    ChatClearResponse,
    ChatThreadResponse,
)
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
)
from src.domain.model.chat import ChatSseEvent
from src.infra.services.durable_write_service import normalize_idempotency_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["Chat"])


@router.get("", response_model=ChatThreadResponse)
async def get_chat_thread(
    request: Request,
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatThreadResponse:
    del request
    payload = await orchestrator.get_thread(user_id=user_id, limit=limit, before=before)
    return ChatThreadResponse.model_validate(payload)


@router.delete("", response_model=ChatClearResponse)
async def clear_chat_thread(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatClearResponse:
    del request
    payload = await orchestrator.clear_thread(user_id)
    return ChatClearResponse.model_validate(payload)


@router.post("/messages")
@limiter.limit("10/minute")
async def post_chat_message(
    request: Request,
    payload: ChatMessageCreateRequest,
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StreamingResponse:
    try:
        idempotency_key = normalize_idempotency_key(idempotency_key_header)
    except ValueError as exc:
        raise ValidationException(
            str(exc), error_code="INVALID_IDEMPOTENCY_KEY"
        ) from exc
    if not idempotency_key:
        raise ValidationException(
            "Idempotency-Key is required",
            error_code="IDEMPOTENCY_KEY_REQUIRED",
        )

    locale = payload.locale or get_request_language(request)
    header_timezone = request.headers.get("X-Timezone")

    try:
        prepared = await orchestrator.prepare_turn(
            user_id=user_id,
            content=payload.content,
            idempotency_key=idempotency_key,
            locale=locale,
            header_timezone=header_timezone,
            user_language=locale,
        )
    except ChatBusyError as exc:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "CHAT_BUSY",
            "A chat turn is already in progress",
            retry_after=exc.retry_after_seconds,
        )
    except ChatIdempotencyConflictError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "CHAT_IDEMPOTENCY_CONFLICT",
            "Idempotency key was reused with a different payload",
        )
    except ChatRateLimitedError as exc:
        return _error_response(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "CHAT_DAILY_LIMIT" if exc.daily else "CHAT_RATE_LIMITED",
            "Chat turn limit exceeded",
            retry_after=exc.retry_after_seconds,
        )
    except ChatProviderUnavailableError as exc:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "CHAT_PROVIDER_UNAVAILABLE",
            "Chat is temporarily unavailable",
            retry_after=exc.retry_after_seconds,
        )

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async for event in orchestrator.stream_prepared(
                user_id=user_id,
                prepared=prepared,
            ):
                yield _encode_sse(event)
        finally:
            orchestrator.release_slot(prepared)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _encode_sse(event: ChatSseEvent) -> bytes:
    payload = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"event: {event.event}\ndata: {payload}\n\n".encode()


def _error_response(
    status_code: int,
    error_code: str,
    message: str,
    *,
    retry_after: int | None = None,
) -> JSONResponse:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {
                "error_code": error_code,
                "message": message,
                "details": {},
            }
        },
        headers=headers,
    )

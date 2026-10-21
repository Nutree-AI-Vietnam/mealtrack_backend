"""Authenticated Nutree coach API.

Thread-scoped routes live under ``/v1/chat/threads``. The original routes
without a thread id keep working on the user's most recently active thread.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing

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
    ChatThreadListResponse,
    ChatThreadResponse,
    ChatThreadSummaryResponse,
)
from src.app.services.chat_turn_orchestrator import ChatTurnOrchestrator
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
    ChatInvalidCursorError,
    ChatProviderUnavailableError,
    ChatRateLimitedError,
    ChatRegenerateNotAllowedError,
    ChatThreadNotFoundError,
)
from src.domain.model.chat import (
    CHAT_THREAD_LIST_DEFAULT_LIMIT,
    CHAT_THREAD_LIST_MAX_LIMIT,
    ChatSseEvent,
)
from src.domain.services.chat.coach_functions import (
    preferred_alias,
    resolve_coach_function,
)
from src.infra.services.durable_write_service import normalize_idempotency_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["Chat"])

# One per-user bucket for both message routes, whichever thread they target.
_MESSAGE_RATE_LIMIT = "10/minute"


@router.get("", response_model=ChatThreadResponse)
async def get_chat_thread(
    request: Request,
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatThreadResponse:
    payload = await orchestrator.get_thread(
        user_id=user_id,
        limit=limit,
        before=before,
        locale=get_request_language(request),
        header_timezone=request.headers.get("X-Timezone"),
    )
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


@router.post("/messages", response_model=None)
@limiter.shared_limit(_MESSAGE_RATE_LIMIT, scope="chat_messages")
async def post_chat_message(
    request: Request,
    payload: ChatMessageCreateRequest,
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StreamingResponse | JSONResponse:
    return await _post_message(
        request,
        payload,
        user_id=user_id,
        orchestrator=orchestrator,
        idempotency_key_header=idempotency_key_header,
        thread_id=None,
    )


@router.get("/threads", response_model=ChatThreadListResponse)
async def list_chat_threads(
    cursor: str | None = Query(
        default=None, description="next_cursor from the previous page."
    ),
    limit: int = Query(
        default=CHAT_THREAD_LIST_DEFAULT_LIMIT,
        ge=1,
        le=CHAT_THREAD_LIST_MAX_LIMIT,
    ),
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatThreadListResponse:
    """Threads that have messages, most recently active first."""
    try:
        payload = await orchestrator.list_threads(
            user_id=user_id, limit=limit, cursor=cursor
        )
    except ChatInvalidCursorError as exc:
        raise ValidationException(
            "Invalid thread cursor", error_code="CHAT_INVALID_CURSOR"
        ) from exc
    return ChatThreadListResponse.model_validate(payload)


@router.post(
    "/threads",
    response_model=ChatThreadSummaryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_thread(
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatThreadSummaryResponse:
    """Start a new thread; an existing empty thread is returned instead."""
    payload = await orchestrator.create_thread(user_id)
    return ChatThreadSummaryResponse.model_validate(payload)


@router.get(
    "/threads/{thread_id}",
    response_model=ChatThreadResponse,
    responses={404: {"description": "CHAT_THREAD_NOT_FOUND"}},
)
async def get_chat_thread_by_id(
    request: Request,
    thread_id: str,
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatThreadResponse | JSONResponse:
    try:
        payload = await orchestrator.get_thread(
            user_id=user_id,
            limit=limit,
            before=before,
            locale=get_request_language(request),
            header_timezone=request.headers.get("X-Timezone"),
            thread_id=thread_id,
        )
    except ChatThreadNotFoundError:
        return _thread_not_found()
    return ChatThreadResponse.model_validate(payload)


@router.delete(
    "/threads/{thread_id}",
    response_model=ChatClearResponse,
    responses={404: {"description": "CHAT_THREAD_NOT_FOUND"}},
)
async def clear_chat_thread_by_id(
    thread_id: str,
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
) -> ChatClearResponse | JSONResponse:
    """Delete the thread's messages; the thread stays so it can be reused."""
    try:
        payload = await orchestrator.clear_thread(user_id, thread_id)
    except ChatThreadNotFoundError:
        return _thread_not_found()
    return ChatClearResponse.model_validate(payload)


@router.post(
    "/threads/{thread_id}/messages",
    response_model=None,
    responses={404: {"description": "CHAT_THREAD_NOT_FOUND"}},
)
@limiter.shared_limit(_MESSAGE_RATE_LIMIT, scope="chat_messages")
async def post_chat_thread_message(
    request: Request,
    thread_id: str,
    payload: ChatMessageCreateRequest,
    user_id: str = Depends(get_current_user_id),
    orchestrator: ChatTurnOrchestrator = Depends(get_chat_turn_orchestrator),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StreamingResponse | JSONResponse:
    return await _post_message(
        request,
        payload,
        user_id=user_id,
        orchestrator=orchestrator,
        idempotency_key_header=idempotency_key_header,
        thread_id=thread_id,
    )


async def _post_message(
    request: Request,
    payload: ChatMessageCreateRequest,
    *,
    user_id: str,
    orchestrator: ChatTurnOrchestrator,
    idempotency_key_header: str | None,
    thread_id: str | None,
) -> StreamingResponse | JSONResponse:
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

    intent_value = payload.intent.value if payload.intent else None
    function_name: str | None = None
    function_args: dict | None = None
    if payload.tool is not None:
        resolved = resolve_coach_function(payload.tool.name, args=payload.tool.args)
        if resolved is None:
            raise ValidationException(
                f"Unknown tool '{payload.tool.name}'",
                error_code="CHAT_UNKNOWN_TOOL",
            )
        # Tool wins over intent when both are present.
        if intent_value is not None and intent_value != preferred_alias(
            resolved.name, resolved.args
        ):
            logger.info(
                "chat tool overrides intent",
                extra={
                    "tool": resolved.name,
                    "ignored_intent": intent_value,
                    "user_id": user_id,
                },
            )
        function_name = resolved.name
        function_args = resolved.args
        intent_value = preferred_alias(resolved.name, resolved.args)

    try:
        if payload.regenerate_assistant_id:
            prepared = await orchestrator.prepare_regeneration(
                user_id=user_id,
                assistant_message_id=payload.regenerate_assistant_id,
                content=payload.content,
                locale=locale,
                header_timezone=header_timezone,
                user_language=locale,
                thread_id=thread_id,
            )
        else:
            prepared = await orchestrator.prepare_turn(
                user_id=user_id,
                content=payload.content,
                idempotency_key=idempotency_key,
                locale=locale,
                header_timezone=header_timezone,
                user_language=locale,
                intent=intent_value,
                function_name=function_name,
                function_args=function_args,
                thread_id=thread_id,
            )
    except ChatThreadNotFoundError:
        return _thread_not_found()
    except ChatRegenerateNotAllowedError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "CHAT_REGENERATE_NOT_ALLOWED",
            "Only the latest coach reply can be regenerated",
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
            async with aclosing(
                orchestrator.stream_prepared(
                    user_id=user_id,
                    prepared=prepared,
                )
            ) as events:
                async for event in events:
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


def _thread_not_found() -> JSONResponse:
    return _error_response(
        status.HTTP_404_NOT_FOUND,
        "CHAT_THREAD_NOT_FOUND",
        "Chat thread not found",
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

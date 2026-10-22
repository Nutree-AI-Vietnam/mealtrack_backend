import asyncio
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.chat import get_chat_turn_orchestrator
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.middleware.rate_limit import limiter, reset_limiter_counters
from src.api.routes.v1 import chat as chat_mod
from src.api.routes.v1.capabilities import router as capabilities_router
from src.app.services.chat_turn_orchestrator import PreparedChatTurn
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
    ChatInvalidCursorError,
    ChatProviderUnavailableError,
    ChatThreadNotFoundError,
)
from src.domain.model.chat import (
    ChatClaimKind,
    ChatMessage,
    ChatMessageRole,
    ChatMessageStatus,
    ChatSseEvent,
    ChatThread,
    ChatTurnClaim,
)
from src.domain.utils.timezone_utils import utc_now


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # Both message routes share one per-user bucket across this module.
    reset_limiter_counters()
    yield
    reset_limiter_counters()


class _StubOrchestrator:
    def __init__(self, *, prepare_error=None, events=None, thread_payload=None):
        self.prepare_error = prepare_error
        self.events = events or []
        self.thread_payload = thread_payload or {
            "thread": {
                "id": "t1",
                "created_at": "2026-09-01T00:00:00+00:00",
                "updated_at": "2026-09-01T00:00:00+00:00",
            },
            "messages": [],
            "has_more": False,
        }
        self.cleared = False
        self.cleared_thread_id = None
        self.prepared = None
        self.prepare_kwargs = None
        self.get_kwargs = None
        self.list_kwargs = None
        self.regenerate_kwargs = None

    def release_slot(self, prepared) -> None:
        return None

    async def get_thread(self, **kwargs):
        self.get_kwargs = kwargs
        if kwargs.get("thread_id") == "missing":
            raise ChatThreadNotFoundError()
        return self.thread_payload

    async def clear_thread(self, user_id: str, thread_id: str | None = None):
        if thread_id == "missing":
            raise ChatThreadNotFoundError()
        self.cleared = True
        self.cleared_thread_id = thread_id
        return {"thread_id": thread_id or "t1", "cleared": True}

    async def list_threads(self, **kwargs):
        self.list_kwargs = kwargs
        if kwargs.get("cursor") == "bad":
            raise ChatInvalidCursorError()
        return {
            "threads": [
                {
                    "id": "t2",
                    "created_at": "2026-09-02T00:00:00+00:00",
                    "updated_at": "2026-09-03T00:00:00+00:00",
                    "title": "What should I eat tonight?",
                    "preview": "Try grilled fish with rice.",
                    "preview_role": "assistant",
                    "last_message_at": "2026-09-03T00:00:00+00:00",
                }
            ],
            "has_more": True,
            "next_cursor": "next-page",
        }

    async def create_thread(self, user_id: str):
        return {
            "id": "t-new",
            "created_at": "2026-09-04T00:00:00+00:00",
            "updated_at": "2026-09-04T00:00:00+00:00",
        }

    async def prepare_regeneration(self, **kwargs):
        self.regenerate_kwargs = kwargs
        return await self.prepare_turn(**{"content": kwargs["content"]})

    async def prepare_turn(self, **kwargs):
        self.prepare_kwargs = kwargs
        if self.prepare_error:
            raise self.prepare_error
        now = utc_now()
        thread = ChatThread(id="t1", user_id="u1", created_at=now, updated_at=now)
        user = ChatMessage(
            id="m-user",
            thread_id="t1",
            role=ChatMessageRole.USER,
            status=ChatMessageStatus.COMPLETED,
            created_at=now,
            updated_at=now,
            content=kwargs["content"],
        )
        assistant = ChatMessage(
            id="m-asst",
            thread_id="t1",
            role=ChatMessageRole.ASSISTANT,
            status=ChatMessageStatus.GENERATING,
            created_at=now,
            updated_at=now,
        )
        claim = ChatTurnClaim(
            kind=ChatClaimKind.NEW,
            thread=thread,
            user_message=user,
            assistant_message=assistant,
        )
        self.prepared = PreparedChatTurn(
            claim=claim,
            content=kwargs["content"],
            locale="en",
            header_timezone=kwargs.get("header_timezone"),
            started=0.0,
        )
        return self.prepared

    async def stream_prepared(self, **kwargs):
        for event in self.events:
            yield event


def _app(orchestrator: _StubOrchestrator) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)
    register_exception_handlers(app)
    app.state.limiter = limiter
    app.include_router(chat_mod.router)
    app.include_router(capabilities_router)
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    app.dependency_overrides[get_chat_turn_orchestrator] = lambda: orchestrator
    return app


def test_get_chat_returns_single_thread():
    client = TestClient(_app(_StubOrchestrator()))
    response = client.get("/v1/chat")
    assert response.status_code == 200
    body = response.json()
    assert body["thread"]["id"] == "t1"
    assert body["messages"] == []


def test_get_chat_defaults_empty_suggestions_and_follow_ups():
    orchestrator = _StubOrchestrator(
        thread_payload={
            "thread": {
                "id": "t1",
                "created_at": "2026-09-01T00:00:00+00:00",
                "updated_at": "2026-09-01T00:00:00+00:00",
            },
            "messages": [
                {
                    "id": "m1",
                    "role": "assistant",
                    "content": "You have 650 remaining.",
                    "created_at": "2026-09-01T00:00:00+00:00",
                    "status": "completed",
                }
            ],
            "has_more": False,
        }
    )
    client = TestClient(_app(orchestrator))
    response = client.get("/v1/chat")
    assert response.status_code == 200
    message = response.json()["messages"][0]
    assert message["suggestions"] == []
    assert message["follow_ups"] == []
    assert message["intent"] is None
    assert message["discover_session_id"] is None
    assert message["nutrition_snapshot"] is None


def test_get_chat_keeps_intent_and_nutrition_snapshot():
    snapshot = {
        "version": "chat_nutrition_v1",
        "as_of": "2026-09-11T12:48:00+00:00",
        "local_date": "2026-09-11",
        "timezone": "Asia/Ho_Chi_Minh",
        "target_calories": 1932,
        "food_calories": 0,
        "movement_kcal_burned": 0,
        "remaining_calories": 1932,
        "remaining_days": 5,
        "macros": {
            "protein": {"consumed_g": 0, "target_g": 140, "remaining_g": 140},
            "carbs": {"consumed_g": 0, "target_g": 250, "remaining_g": 250},
            "fat": {"consumed_g": 0, "target_g": 70, "remaining_g": 70},
        },
    }
    orchestrator = _StubOrchestrator(
        thread_payload={
            "thread": {
                "id": "t1",
                "created_at": "2026-09-01T00:00:00+00:00",
                "updated_at": "2026-09-01T00:00:00+00:00",
            },
            "messages": [
                {
                    "id": "m1",
                    "role": "assistant",
                    "content": "You have 1932 remaining.",
                    "created_at": "2026-09-11T12:48:00+00:00",
                    "status": "completed",
                    "intent": "remaining_budget",
                    "nutrition_snapshot": snapshot,
                }
            ],
            "has_more": False,
        }
    )
    client = TestClient(_app(orchestrator))
    response = client.get("/v1/chat")
    assert response.status_code == 200
    message = response.json()["messages"][0]
    assert message["intent"] == "remaining_budget"
    assert message["nutrition_snapshot"]["food_calories"] == 0
    assert message["nutrition_snapshot"]["remaining_calories"] == 1932


def test_delete_chat_clears_messages():
    orchestrator = _StubOrchestrator()
    client = TestClient(_app(orchestrator))
    response = client.delete("/v1/chat")
    assert response.status_code == 200
    assert response.json()["cleared"] is True
    assert orchestrator.cleared is True


def test_post_requires_idempotency_key():
    client = TestClient(_app(_StubOrchestrator()))
    response = client.post("/v1/chat/messages", json={"content": "Hello"})
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_post_busy_returns_409():
    client = TestClient(_app(_StubOrchestrator(prepare_error=ChatBusyError())))
    response = client.post(
        "/v1/chat/messages",
        json={"content": "Hello"},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "CHAT_BUSY"
    assert response.headers["Retry-After"] == "5"


def test_post_idempotency_conflict_returns_409():
    client = TestClient(
        _app(_StubOrchestrator(prepare_error=ChatIdempotencyConflictError()))
    )
    response = client.post(
        "/v1/chat/messages",
        json={"content": "Hello"},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "CHAT_IDEMPOTENCY_CONFLICT"


def test_post_provider_unavailable_returns_503():
    client = TestClient(
        _app(_StubOrchestrator(prepare_error=ChatProviderUnavailableError()))
    )
    response = client.post(
        "/v1/chat/messages",
        json={"content": "Hello"},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "CHAT_PROVIDER_UNAVAILABLE"
    assert response.headers["Retry-After"] == "15"


def test_post_streams_sse_events():
    events = [
        ChatSseEvent(
            event="message.started",
            data={
                "thread_id": "t1",
                "user_message_id": "m-user",
                "assistant_message_id": "m-asst",
            },
        ),
        ChatSseEvent(
            event="message.delta",
            data={"assistant_message_id": "m-asst", "delta": "Hello. "},
        ),
        ChatSseEvent(
            event="message.completed",
            data={"assistant_message_id": "m-asst", "model": "gpt-5.6-luna"},
        ),
    ]
    client = TestClient(_app(_StubOrchestrator(events=events)))
    response = client.post(
        "/v1/chat/messages",
        json={"content": "Hello"},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: message.started" in body
    assert "event: message.delta" in body
    assert "event: message.completed" in body
    assert "Hello. " in body


def test_chat_capabilities_advertise_multi_thread_after_migration():
    with (
        patch(
            "src.api.routes.v1.capabilities.chat_schema_is_ready",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.api.routes.v1.capabilities.chat_thread_model",
            new=AsyncMock(return_value="multi"),
        ),
    ):
        client = TestClient(_app(_StubOrchestrator()))
        response = client.get("/v1/capabilities/chat")
    assert response.status_code == 200
    assert response.json()["thread_model"] == "multi"


def test_chat_capabilities_advertise_single_thread_contract():
    with (
        patch(
            "src.api.routes.v1.capabilities.chat_schema_is_ready",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "src.api.routes.v1.capabilities.chat_thread_model",
            new=AsyncMock(return_value="single"),
        ),
    ):
        client = TestClient(_app(_StubOrchestrator()))
        response = client.get("/v1/capabilities/chat")
    assert response.status_code == 200
    body = response.json()
    assert body["thread_model"] == "single"
    assert body["read_only"] is True
    assert body["sse"] is True
    assert body["header"] == "Idempotency-Key"
    assert body["default_model"] == "gpt-5.6-luna"
    assert body["escalation_enabled"] is False
    assert body["max_user_message_chars"] == 4000
    assert body["daily_turn_budget"] == 40
    assert body["generation_lease_seconds"] == 90
    assert body["intents"] == [
        "remaining_budget",
        "next_meal",
        "day_progress",
        "limits",
    ]
    assert body["functions"] == [
        {
            "name": "check_daily_progress",
            "aliases": ["remaining_budget", "day_progress"],
            "direct_invoke": True,
            "needs_retrieval": False,
        },
        {
            "name": "suggest_next_meal",
            "aliases": ["next_meal"],
            "direct_invoke": True,
            "needs_retrieval": False,
        },
        {
            "name": "explain_limits_and_guidelines",
            "aliases": ["limits"],
            "direct_invoke": True,
            "needs_retrieval": False,
        },
        {
            "name": "search_nutrition_knowledge",
            "aliases": [],
            "direct_invoke": True,
            "needs_retrieval": True,
        },
    ]
    assert "CHAT_BUSY" in body["error_codes"]
    assert "CHAT_UNAVAILABLE" in body["error_codes"]


def test_chat_capabilities_unavailable_when_schema_missing():
    with patch(
        "src.api.routes.v1.capabilities.chat_schema_is_ready",
        new=AsyncMock(return_value=False),
    ):
        client = TestClient(_app(_StubOrchestrator()))
        response = client.get("/v1/capabilities/chat")
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "CHAT_UNAVAILABLE"


def test_post_forwards_structured_intent():
    orchestrator = _StubOrchestrator(
        events=[
            ChatSseEvent(
                event="message.started",
                data={"thread_id": "t1"},
            )
        ]
    )
    client = TestClient(_app(orchestrator))
    response = client.post(
        "/v1/chat/messages",
        json={"content": "What's left?", "intent": "remaining_budget"},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 200
    assert orchestrator.prepare_kwargs["intent"] == "remaining_budget"
    assert orchestrator.prepare_kwargs["function_name"] is None


def test_post_forwards_tool_and_preferred_alias():
    orchestrator = _StubOrchestrator(
        events=[
            ChatSseEvent(
                event="message.started",
                data={"thread_id": "t1"},
            )
        ]
    )
    client = TestClient(_app(orchestrator))
    response = client.post(
        "/v1/chat/messages",
        json={
            "content": "What's left?",
            "tool": {
                "name": "check_daily_progress",
                "args": {"focus": "remaining_budget"},
            },
        },
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 200
    assert orchestrator.prepare_kwargs["intent"] == "remaining_budget"
    assert orchestrator.prepare_kwargs["function_name"] == "check_daily_progress"
    assert orchestrator.prepare_kwargs["function_args"] == {"focus": "remaining_budget"}


def test_post_tool_wins_over_conflicting_intent():
    orchestrator = _StubOrchestrator(
        events=[
            ChatSseEvent(
                event="message.started",
                data={"thread_id": "t1"},
            )
        ]
    )
    client = TestClient(_app(orchestrator))
    response = client.post(
        "/v1/chat/messages",
        json={
            "content": "Suggest something",
            "intent": "limits",
            "tool": {"name": "suggest_next_meal"},
        },
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 200
    assert orchestrator.prepare_kwargs["intent"] == "next_meal"
    assert orchestrator.prepare_kwargs["function_name"] == "suggest_next_meal"


def test_post_rejects_unknown_tool():
    client = TestClient(_app(_StubOrchestrator()))
    response = client.post(
        "/v1/chat/messages",
        json={"content": "Search", "tool": {"name": "web_search_recipes"}},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "CHAT_UNKNOWN_TOOL"


def test_post_rejects_unknown_intent():
    client = TestClient(_app(_StubOrchestrator()))
    response = client.post(
        "/v1/chat/messages",
        json={"content": "What's left?", "intent": "log_meal"},
        headers={"Idempotency-Key": "k1"},
    )
    assert response.status_code == 422


def test_get_chat_passes_through_in_flight():
    orchestrator = _StubOrchestrator(
        thread_payload={
            "thread": {
                "id": "t1",
                "created_at": "2026-09-01T00:00:00+00:00",
                "updated_at": "2026-09-01T00:00:00+00:00",
            },
            "messages": [],
            "has_more": False,
            "in_flight": {
                "user_message": {
                    "id": "m-user",
                    "role": "user",
                    "content": "What's left?",
                    "created_at": "2026-09-01T00:00:00+00:00",
                },
                "assistant_message_id": "m-asst",
                "idempotency_key": "k1",
                "lease_expires_at": "2026-09-01T00:01:30+00:00",
            },
        }
    )
    client = TestClient(_app(orchestrator))
    response = client.get("/v1/chat")
    assert response.status_code == 200
    body = response.json()
    assert body["in_flight"]["assistant_message_id"] == "m-asst"
    assert body["in_flight"]["idempotency_key"] == "k1"


def test_list_threads_returns_titles_and_previews():
    orchestrator = _StubOrchestrator()
    client = TestClient(_app(orchestrator))
    response = client.get("/v1/chat/threads?limit=5&cursor=c9")
    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is True
    assert body["threads"][0]["id"] == "t2"
    assert body["threads"][0]["title"] == "What should I eat tonight?"
    assert body["threads"][0]["preview_role"] == "assistant"
    assert body["next_cursor"] == "next-page"
    assert orchestrator.list_kwargs == {"user_id": "u1", "limit": 5, "cursor": "c9"}


def test_list_threads_rejects_oversized_page():
    client = TestClient(_app(_StubOrchestrator()))
    assert client.get("/v1/chat/threads?limit=51").status_code == 422


def test_create_thread_returns_created_thread():
    client = TestClient(_app(_StubOrchestrator()))
    response = client.post("/v1/chat/threads")
    assert response.status_code == 201
    assert response.json()["id"] == "t-new"


def test_get_thread_by_id_passes_thread_id():
    orchestrator = _StubOrchestrator()
    client = TestClient(_app(orchestrator))
    response = client.get("/v1/chat/threads/t1?limit=10")
    assert response.status_code == 200
    assert response.json()["thread"]["id"] == "t1"
    assert orchestrator.get_kwargs["thread_id"] == "t1"
    assert orchestrator.get_kwargs["limit"] == 10


def test_legacy_get_reads_latest_thread():
    orchestrator = _StubOrchestrator()
    client = TestClient(_app(orchestrator))
    assert client.get("/v1/chat").status_code == 200
    assert orchestrator.get_kwargs.get("thread_id") is None


def test_unknown_thread_is_not_found():
    client = TestClient(_app(_StubOrchestrator()))
    response = client.get("/v1/chat/threads/missing")
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "CHAT_THREAD_NOT_FOUND"
    response = client.delete("/v1/chat/threads/missing")
    assert response.status_code == 404


def test_clear_thread_by_id():
    orchestrator = _StubOrchestrator()
    client = TestClient(_app(orchestrator))
    response = client.delete("/v1/chat/threads/t1")
    assert response.status_code == 200
    assert response.json() == {"thread_id": "t1", "cleared": True}
    assert orchestrator.cleared_thread_id == "t1"


def test_post_to_thread_passes_thread_id():
    orchestrator = _StubOrchestrator(
        events=[ChatSseEvent(event="message.completed", data={"thread_id": "t1"})]
    )
    client = TestClient(_app(orchestrator))
    response = client.post(
        "/v1/chat/threads/t1/messages",
        json={"content": "Hi"},
        headers={"Idempotency-Key": "thread-key-1"},
    )
    assert response.status_code == 200
    assert orchestrator.prepare_kwargs["thread_id"] == "t1"


def test_regenerate_in_thread_passes_thread_id():
    orchestrator = _StubOrchestrator(events=[])
    client = TestClient(_app(orchestrator))
    response = client.post(
        "/v1/chat/threads/t1/messages",
        json={"content": "Hi", "regenerate_assistant_id": "m-asst"},
        headers={"Idempotency-Key": "thread-key-2"},
    )
    assert response.status_code == 200
    assert orchestrator.regenerate_kwargs["thread_id"] == "t1"


def test_post_to_unknown_thread_is_not_found():
    orchestrator = _StubOrchestrator(prepare_error=ChatThreadNotFoundError())
    client = TestClient(_app(orchestrator))
    response = client.post(
        "/v1/chat/threads/missing/messages",
        json={"content": "Hi"},
        headers={"Idempotency-Key": "thread-key-3"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "CHAT_THREAD_NOT_FOUND"


def test_message_routes_share_one_rate_limit():
    orchestrator = _StubOrchestrator(events=[])
    client = TestClient(_app(orchestrator))
    statuses = []
    for index in range(11):
        path = "/v1/chat/messages" if index % 2 else "/v1/chat/threads/t1/messages"
        response = client.post(
            path,
            json={"content": "Hi"},
            headers={"Idempotency-Key": f"shared-limit-{index}"},
        )
        statuses.append(response.status_code)
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429


@pytest.mark.asyncio
async def test_client_disconnect_acloses_prepared_stream():
    class _Body:
        def __init__(self) -> None:
            self.closed = False
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                await asyncio.Event().wait()
            self._sent = True
            return ChatSseEvent(
                event="message.started",
                data={"thread_id": "t1", "assistant_message_id": "m-asst"},
            )

        async def aclose(self) -> None:
            self.closed = True

    class _Orchestrator(_StubOrchestrator):
        def __init__(self) -> None:
            super().__init__()
            self.body = _Body()

        def stream_prepared(self, **kwargs):
            del kwargs
            return self.body

    orchestrator = _Orchestrator()
    request_body = b'{"content": "Hello"}'
    first_event_sent = asyncio.Event()
    sent: list[dict] = []
    received = 0

    async def receive() -> dict:
        nonlocal received
        received += 1
        if received == 1:
            return {"type": "http.request", "body": request_body, "more_body": False}
        # Uvicorn reports a client that hangs up mid-stream as http.disconnect.
        # TestClient before Starlette 1.9 sends it only once the body ends.
        await first_event_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_event_sent.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/messages",
        "raw_path": b"/v1/chat/messages",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(request_body)).encode()),
            (b"idempotency-key", b"k-close"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    with anyio.fail_after(5):
        await _app(orchestrator)(scope, receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200
    assert b"event: message.started" in sent[1]["body"]
    # Do not gc.collect(): disconnect must aclose the orchestrator stream.
    assert orchestrator.body.closed is True


def test_list_threads_rejects_unknown_cursor():
    client = TestClient(_app(_StubOrchestrator()))
    response = client.get("/v1/chat/threads?cursor=bad")
    assert response.status_code in (400, 422)
    assert "CHAT_INVALID_CURSOR" in response.text


def test_regenerate_in_unknown_thread_is_not_found():
    orchestrator = _StubOrchestrator(prepare_error=ChatThreadNotFoundError())
    client = TestClient(_app(orchestrator))
    response = client.post(
        "/v1/chat/threads/missing/messages",
        json={"content": "Hi", "regenerate_assistant_id": "m-asst"},
        headers={"Idempotency-Key": "thread-key-4"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "CHAT_THREAD_NOT_FOUND"

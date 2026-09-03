from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.chat import get_chat_turn_orchestrator
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1 import chat as chat_mod
from src.api.routes.v1.capabilities import router as capabilities_router
from src.app.services.chat_turn_orchestrator import PreparedChatTurn
from src.domain.exceptions.chat_exceptions import (
    ChatBusyError,
    ChatIdempotencyConflictError,
    ChatProviderUnavailableError,
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
        self.prepared = None
        self.prepare_kwargs = None

    def release_slot(self, prepared) -> None:
        return None

    async def get_thread(self, **kwargs):
        return self.thread_payload

    async def clear_thread(self, user_id: str):
        self.cleared = True
        return {"thread_id": "t1", "cleared": True}

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


def test_chat_capabilities_advertise_single_thread_contract():
    with patch(
        "src.api.routes.v1.capabilities.chat_schema_is_ready",
        new=AsyncMock(return_value=True),
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

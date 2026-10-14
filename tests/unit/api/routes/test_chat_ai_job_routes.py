"""Chat turns sent with ``ai_job_id``, and the job id of a turn in flight."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from tests.fixtures.fakes.fake_ai_jobs import JOB_ID, make_claim

from src.api.dependencies.ai_jobs import get_ai_job_service, get_chat_job_runner
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.chat import get_chat_turn_orchestrator
from src.api.exception_handlers import register_exception_handlers
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
from src.api.routes.v1 import chat as chat_mod
from src.app.services.chat_turn_orchestrator import PreparedChatTurn
from src.domain.exceptions.ai_job_exceptions import (
    AiJobConflictError,
    AiJobsUnavailableError,
)
from src.domain.exceptions.chat_exceptions import ChatBusyError
from src.domain.model.ai_jobs import AiJobType, NewAiJob
from src.domain.model.ai_jobs.policies import ai_job_request_fingerprint
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

ORCHESTRATOR_EVENTS = [
    ChatSseEvent(event="message.started", data={"source": "orchestrator"})
]
RUNNER_EVENTS = [
    ChatSseEvent(event="message.started", data={"thread_id": "t1"}),
    ChatSseEvent(event="message.delta", data={"delta": "Còn 650 kcal"}),
]
RUNNER_BODY = (
    'event: message.started\ndata: {"thread_id": "t1"}\n\n'
    'event: message.delta\ndata: {"delta": "Còn 650 kcal"}\n\n'
)
ORCHESTRATOR_BODY = 'event: message.started\ndata: {"source": "orchestrator"}\n\n'


class _StubOrchestrator:
    """The chat route tests' stub, plus ``abandon_turn`` and call records."""

    def __init__(
        self,
        *,
        prepare_error=None,
        claim_kind=ChatClaimKind.NEW,
        thread_payload=None,
    ):
        self.prepare_error = prepare_error
        self.claim_kind = claim_kind
        self.events = ORCHESTRATOR_EVENTS
        self.thread_payload = thread_payload or _thread_payload()
        self.prepared: PreparedChatTurn | None = None
        self.prepare_kwargs: dict | None = None
        self.regenerated: str | None = None
        self.streamed = False
        self.released: list[PreparedChatTurn] = []
        self.abandoned: list[tuple[PreparedChatTurn, str]] = []

    def release_slot(self, prepared) -> None:
        self.released.append(prepared)

    async def abandon_turn(self, prepared, *, error_code: str) -> None:
        self.abandoned.append((prepared, error_code))

    async def get_thread(self, **kwargs):
        return self.thread_payload

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
            kind=self.claim_kind,
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

    async def prepare_regeneration(self, **kwargs):
        self.regenerated = kwargs.pop("assistant_message_id")
        return await self.prepare_turn(**kwargs)

    async def stream_prepared(self, **kwargs):
        self.streamed = True
        for event in self.events:
            yield event


class _StubAiJobs:
    def __init__(self, *, claim=None, error=None, active_job_id=None):
        self.start_chat_job = AsyncMock(return_value=claim, side_effect=error)
        self.find_active_chat_job_id = AsyncMock(return_value=active_job_id)


class _StubRunner:
    def __init__(self) -> None:
        self.session = object()
        self.start_kwargs: dict | None = None
        self.streamed_session = None
        self.closed = False

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        return self.session

    async def stream(self, session):
        self.streamed_session = session
        try:
            for event in RUNNER_EVENTS:
                yield event
        finally:
            self.closed = True


@pytest.fixture(autouse=True)
def _reset_limiter():
    # Chat allows 10 messages a minute and the chat route tests never reset.
    limiter._storage.reset()
    yield
    limiter._storage.reset()


def _app(orchestrator, ai_jobs, runner) -> FastAPI:
    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)
    register_exception_handlers(app)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(chat_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    app.dependency_overrides[get_chat_turn_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_ai_job_service] = lambda: ai_jobs
    app.dependency_overrides[get_chat_job_runner] = lambda: runner
    return app


def _post(orchestrator, ai_jobs, runner, *, headers=None, **body):
    client = TestClient(_app(orchestrator, ai_jobs, runner))
    return client.post(
        "/v1/chat/messages",
        json={"content": "What's left?", **body},
        headers={"Idempotency-Key": "k1", **(headers or {})},
    )


def _chat_request(**overrides) -> dict:
    return {
        "content": "What's left?",
        "idempotency_key": "k1",
        "intent": None,
        "function_name": None,
        "function_args": None,
        **overrides,
    }


def _thread_payload(in_flight=None) -> dict:
    payload = {
        "thread": {
            "id": "t1",
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
        },
        "messages": [],
        "has_more": False,
    }
    if in_flight is not None:
        payload["in_flight"] = in_flight
    return payload


def _in_flight(**overrides) -> dict:
    return {
        "user_message": {
            "id": "m-user",
            "role": "user",
            "content": "What's left?",
            "created_at": "2026-09-01T00:00:00+00:00",
        },
        "assistant_message_id": "m-asst",
        "idempotency_key": "k1",
        "lease_expires_at": "2026-09-01T00:01:30+00:00",
        **overrides,
    }


class TestPostWithJob:
    def test_claimed_job_streams_the_runner_events(self):
        orchestrator = _StubOrchestrator()
        claim = make_claim(job_type=AiJobType.CHAT)
        ai_jobs = _StubAiJobs(claim=claim)
        runner = _StubRunner()

        response = _post(
            orchestrator,
            ai_jobs,
            runner,
            content="  What's left?  ",
            ai_job_id=JOB_ID.upper(),
            headers={"Accept-Language": "vi", "X-Timezone": "Asia/Ho_Chi_Minh"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text == RUNNER_BODY
        assert runner.start_kwargs == {
            "orchestrator": orchestrator,
            "claim": claim,
            "prepared": orchestrator.prepared,
        }
        assert runner.streamed_session is runner.session
        assert runner.closed is True
        # The runner owns the provider slot from here on.
        assert orchestrator.released == []
        assert orchestrator.streamed is False
        (job,) = ai_jobs.start_chat_job.await_args.args
        request = _chat_request()
        assert job == NewAiJob(
            id=JOB_ID,
            user_id="u1",
            job_type=AiJobType.CHAT,
            # The chat locale the turn resolved, not the raw request locale.
            language="en",
            request_payload={
                **request,
                "locale": "vi",
                "header_timezone": "Asia/Ho_Chi_Minh",
                "user_language": "vi",
            },
            request_fingerprint=ai_job_request_fingerprint(AiJobType.CHAT, request),
            dedupe_key="k1",
        )

    def test_job_records_the_tool_and_the_locale_the_turn_used(self):
        ai_jobs = _StubAiJobs(claim=make_claim(job_type=AiJobType.CHAT))

        response = _post(
            _StubOrchestrator(),
            ai_jobs,
            _StubRunner(),
            ai_job_id=JOB_ID,
            locale="es",
            intent="limits",
            tool={
                "name": "check_daily_progress",
                "args": {"focus": "remaining_budget"},
            },
            headers={"Accept-Language": "vi"},
        )

        assert response.status_code == 200
        (job,) = ai_jobs.start_chat_job.await_args.args
        request = _chat_request(
            intent="remaining_budget",
            function_name="check_daily_progress",
            function_args={"focus": "remaining_budget"},
        )
        assert job.request_payload == {
            **request,
            "locale": "es",
            "header_timezone": None,
            "user_language": "es",
        }
        assert job.request_fingerprint == ai_job_request_fingerprint(
            AiJobType.CHAT, request
        )

    @pytest.mark.parametrize(
        "ai_jobs",
        [
            pytest.param(
                lambda: _StubAiJobs(error=AiJobsUnavailableError("chat_disabled")),
                id="chat-jobs-off",
            ),
            pytest.param(
                lambda: _StubAiJobs(error=RuntimeError("database is down")),
                id="unexpected-error",
            ),
            pytest.param(lambda: _StubAiJobs(claim=None), id="no-claim"),
        ],
    )
    def test_turn_without_a_claim_streams_directly(self, ai_jobs):
        orchestrator = _StubOrchestrator()
        ai_jobs = ai_jobs()
        runner = _StubRunner()

        response = _post(orchestrator, ai_jobs, runner, ai_job_id=JOB_ID)

        assert response.status_code == 200
        assert response.text == ORCHESTRATOR_BODY
        ai_jobs.start_chat_job.assert_awaited_once()
        assert runner.start_kwargs is None
        assert orchestrator.released == [orchestrator.prepared]
        assert orchestrator.abandoned == []

    def test_unexpected_job_error_is_logged(self, caplog):
        caplog.set_level(logging.WARNING, logger=chat_mod.logger.name)

        response = _post(
            _StubOrchestrator(),
            _StubAiJobs(error=RuntimeError("database is down")),
            _StubRunner(),
            ai_job_id=JOB_ID,
        )

        assert response.status_code == 200
        (record,) = [r for r in caplog.records if r.name == chat_mod.logger.name]
        assert record.getMessage() == "Chat turn streams without an AI job"
        assert isinstance(record.exc_info[1], RuntimeError)

    def test_job_id_used_by_another_request_abandons_the_turn(self):
        orchestrator = _StubOrchestrator()
        runner = _StubRunner()

        response = _post(
            orchestrator,
            _StubAiJobs(error=AiJobConflictError(JOB_ID)),
            runner,
            ai_job_id=JOB_ID,
        )

        assert response.status_code == 409
        assert response.json() == {
            "detail": {
                "error_code": "AI_JOB_CONFLICT",
                "message": "This job id is already used by a different request",
                "details": {},
            }
        }
        assert "Retry-After" not in response.headers
        assert orchestrator.abandoned == [(orchestrator.prepared, "CHAT_TURN_FAILED")]
        # abandon_turn frees the slot itself.
        assert orchestrator.released == []
        assert orchestrator.streamed is False
        assert runner.start_kwargs is None

    def test_replayed_turn_does_not_start_a_job(self):
        orchestrator = _StubOrchestrator(claim_kind=ChatClaimKind.REPLAY)
        ai_jobs = _StubAiJobs(claim=make_claim(job_type=AiJobType.CHAT))
        runner = _StubRunner()

        response = _post(orchestrator, ai_jobs, runner, ai_job_id=JOB_ID)

        assert response.text == ORCHESTRATOR_BODY
        ai_jobs.start_chat_job.assert_not_awaited()
        assert runner.start_kwargs is None
        assert orchestrator.released == [orchestrator.prepared]

    def test_turn_that_is_not_prepared_starts_no_job(self):
        orchestrator = _StubOrchestrator(prepare_error=ChatBusyError())
        ai_jobs = _StubAiJobs(claim=make_claim(job_type=AiJobType.CHAT))

        response = _post(orchestrator, ai_jobs, _StubRunner(), ai_job_id=JOB_ID)

        assert response.status_code == 409
        assert response.json()["detail"]["error_code"] == "CHAT_BUSY"
        ai_jobs.start_chat_job.assert_not_awaited()

    def test_regenerated_reply_streams_without_a_job(self):
        orchestrator = _StubOrchestrator()
        ai_jobs = _StubAiJobs(claim=make_claim(job_type=AiJobType.CHAT))
        runner = _StubRunner()

        response = _post(
            orchestrator,
            ai_jobs,
            runner,
            ai_job_id=JOB_ID,
            regenerate_assistant_id="m-asst",
        )

        assert response.status_code == 200
        assert response.text == ORCHESTRATOR_BODY
        assert orchestrator.regenerated == "m-asst"
        ai_jobs.start_chat_job.assert_not_awaited()
        assert runner.start_kwargs is None
        assert orchestrator.released == [orchestrator.prepared]

    def test_message_without_a_job_id_streams_plainly(self):
        orchestrator = _StubOrchestrator()
        ai_jobs = _StubAiJobs(claim=make_claim(job_type=AiJobType.CHAT))
        runner = _StubRunner()

        response = _post(orchestrator, ai_jobs, runner)

        assert response.text == ORCHESTRATOR_BODY
        ai_jobs.start_chat_job.assert_not_awaited()
        assert runner.start_kwargs is None
        assert orchestrator.released == [orchestrator.prepared]

    def test_job_id_must_be_a_uuid(self):
        orchestrator = _StubOrchestrator()
        ai_jobs = _StubAiJobs()

        response = _post(orchestrator, ai_jobs, _StubRunner(), ai_job_id="job-1")

        assert response.status_code == 422
        assert [error["loc"] for error in response.json()["detail"]] == [
            ["body", "ai_job_id"]
        ]
        assert orchestrator.prepare_kwargs is None
        ai_jobs.start_chat_job.assert_not_awaited()


class TestThreadInFlight:
    def test_turn_in_flight_reports_its_job(self):
        ai_jobs = _StubAiJobs(active_job_id=JOB_ID)
        orchestrator = _StubOrchestrator(thread_payload=_thread_payload(_in_flight()))
        client = TestClient(_app(orchestrator, ai_jobs, _StubRunner()))

        response = client.get("/v1/chat")

        assert response.status_code == 200
        in_flight = response.json()["in_flight"]
        assert in_flight["ai_job_id"] == JOB_ID
        assert in_flight["idempotency_key"] == "k1"
        ai_jobs.find_active_chat_job_id.assert_awaited_once_with(
            user_id="u1", dedupe_key="k1"
        )

    def test_turn_in_flight_without_a_job(self):
        ai_jobs = _StubAiJobs(active_job_id=None)
        orchestrator = _StubOrchestrator(thread_payload=_thread_payload(_in_flight()))
        client = TestClient(_app(orchestrator, ai_jobs, _StubRunner()))

        response = client.get("/v1/chat")

        assert response.json()["in_flight"]["ai_job_id"] is None
        ai_jobs.find_active_chat_job_id.assert_awaited_once()

    @pytest.mark.parametrize(
        "in_flight",
        [
            pytest.param(_in_flight(idempotency_key=None), id="no-key"),
            pytest.param(_in_flight(idempotency_key=""), id="empty-key"),
        ],
    )
    def test_turn_without_an_idempotency_key_is_not_looked_up(self, in_flight):
        ai_jobs = _StubAiJobs(active_job_id=JOB_ID)
        orchestrator = _StubOrchestrator(thread_payload=_thread_payload(in_flight))
        client = TestClient(_app(orchestrator, ai_jobs, _StubRunner()))

        response = client.get("/v1/chat")

        assert response.status_code == 200
        assert response.json()["in_flight"]["ai_job_id"] is None
        ai_jobs.find_active_chat_job_id.assert_not_awaited()

    def test_idle_thread_is_not_looked_up(self):
        ai_jobs = _StubAiJobs(active_job_id=JOB_ID)
        client = TestClient(_app(_StubOrchestrator(), ai_jobs, _StubRunner()))

        response = client.get("/v1/chat")

        assert response.status_code == 200
        assert response.json()["in_flight"] is None
        ai_jobs.find_active_chat_job_id.assert_not_awaited()

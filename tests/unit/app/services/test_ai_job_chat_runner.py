"""ChatJobRunner streams a chat job's turn and records how the attempt ended."""

import asyncio
import logging

import pytest
import pytest_asyncio
from tests.fixtures.fakes.fake_ai_jobs import (
    JOB_ID,
    NOW,
    USER_ID,
    FakeAiJobsUow,
    make_claim,
    make_job,
)

from src.app.services.ai_jobs.chat_runner import ChatJobRunner, ChatJobSession
from src.app.services.ai_jobs.executors import CHAT_TERMINAL_EVENTS
from src.app.services.ai_jobs.signals import AiJobCancelSignals
from src.app.services.chat_turn_orchestrator import PreparedChatTurn
from src.domain.exceptions.chat_exceptions import ChatRateLimitedError
from src.domain.model.ai_jobs import (
    AiJobError,
    AiJobErrorKind,
    AiJobHeartbeat,
    AiJobStatus,
    AiJobType,
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

LEASE_SECONDS = 120
GRACE_SECONDS = 8
# retry_backoff_seconds(1) with the jitter pinned to its midpoint.
FIRST_RETRY_DELAY = 5.0
TRANSIENT = AiJobErrorKind.TRANSIENT
PERMANENT = AiJobErrorKind.PERMANENT


@pytest_asyncio.fixture(autouse=True)
async def no_leaked_tasks():
    yield
    current = asyncio.current_task()
    leftover = {task for task in asyncio.all_tasks() if task is not current}
    if leftover:
        _, pending = await asyncio.wait(leftover, timeout=2)
        assert not pending, f"tasks outlived the test: {pending}"


class FakeOrchestrator:
    """Streams scripted events and records the cleanup calls it gets."""

    def __init__(
        self,
        events: list[ChatSseEvent],
        *,
        gate: asyncio.Event | None = None,
        error: BaseException | None = None,
        hang: bool = False,
    ) -> None:
        self.events = events
        # Holds terminal events back until set.
        self.gate = gate
        self.error = error
        # Waits after the last event until cancelled.
        self.hang = hang
        self.streamed: list[tuple[str, PreparedChatTurn]] = []
        self.stream_closed = False
        self.abandoned: list[str] = []
        self.abandoned_event = asyncio.Event()
        self.released: list[PreparedChatTurn] = []

    async def stream_prepared(self, *, user_id: str, prepared: PreparedChatTurn):
        self.streamed.append((user_id, prepared))
        try:
            for event in self.events:
                if self.gate is not None and event.event in CHAT_TERMINAL_EVENTS:
                    await self.gate.wait()
                yield event
            if self.error is not None:
                raise self.error
            if self.hang:
                await asyncio.Event().wait()
        finally:
            self.stream_closed = True

    async def abandon_turn(
        self, prepared: PreparedChatTurn, *, error_code: str
    ) -> None:
        self.abandoned.append(error_code)
        self.abandoned_event.set()

    def release_slot(self, prepared: PreparedChatTurn) -> None:
        self.released.append(prepared)


def _prepared() -> PreparedChatTurn:
    thread = ChatThread(id="t1", user_id=USER_ID, created_at=NOW, updated_at=NOW)
    user_message = ChatMessage(
        id="m-user",
        thread_id="t1",
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
        content="hi",
    )
    assistant_message = ChatMessage(
        id="m-asst",
        thread_id="t1",
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.GENERATING,
        created_at=NOW,
        updated_at=NOW,
    )
    return PreparedChatTurn(
        claim=ChatTurnClaim(
            kind=ChatClaimKind.NEW,
            thread=thread,
            user_message=user_message,
            assistant_message=assistant_message,
        ),
        content="hi",
        locale="en",
        header_timezone=None,
        started=0.0,
    )


def _claim():
    return make_claim(job_type=AiJobType.CHAT, request_payload={"content": "hi"})


def _uow() -> FakeAiJobsUow:
    uow = FakeAiJobsUow()
    jobs = uow.ai_jobs
    jobs.heartbeat.return_value = AiJobHeartbeat(renewed=True, cancel_requested=False)
    jobs.complete.return_value = True
    jobs.fail_attempt.return_value = AiJobStatus.QUEUED
    jobs.cancel_claimed.return_value = True
    jobs.get.return_value = make_job(
        job_type=AiJobType.CHAT, status=AiJobStatus.RUNNING
    )
    return uow


def _runner(
    uow: FakeAiJobsUow,
    signals: AiJobCancelSignals | None = None,
    *,
    deadline_seconds: float = 5.0,
) -> ChatJobRunner:
    return ChatJobRunner(
        uow_factory=uow,
        cancel_signals=signals if signals is not None else AiJobCancelSignals(),
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=0.01,
        deadline_seconds=deadline_seconds,
        notify_grace_seconds=GRACE_SECONDS,
        rng=lambda: 0.5,
    )


def _start(runner: ChatJobRunner, orchestrator: FakeOrchestrator) -> ChatJobSession:
    return runner.start(orchestrator=orchestrator, claim=_claim(), prepared=_prepared())


async def _collect(
    runner: ChatJobRunner, session: ChatJobSession
) -> list[ChatSseEvent]:
    async with asyncio.timeout(2):
        return [event async for event in runner.stream(session)]


def _started() -> ChatSseEvent:
    return ChatSseEvent(
        "message.started", {"thread_id": "t1", "assistant_message_id": "m-asst"}
    )


def _delta() -> ChatSseEvent:
    return ChatSseEvent("message.delta", {"text": "Hi"})


def _completed() -> ChatSseEvent:
    return ChatSseEvent(
        "message.completed",
        {"thread_id": "t1", "assistant_message_id": "m-asst", "content": "Hi"},
    )


def _turn_error(code: str = "CHAT_PROVIDER_UNAVAILABLE") -> ChatSseEvent:
    """A terminal error sent by the orchestrator."""
    return ChatSseEvent(
        "message.error",
        {"code": code, "retryable": True, "assistant_message_id": "m-asst"},
    )


def _runner_error(code: str, *, retryable: bool, status: str | None) -> ChatSseEvent:
    """A terminal error the runner builds itself."""
    return ChatSseEvent(
        "message.error",
        {
            "code": code,
            "retryable": retryable,
            "assistant_message_id": "m-asst",
            "ai_job_id": JOB_ID,
            "ai_job_status": status,
        },
    )


def _failure(code: str, kind: AiJobErrorKind = TRANSIENT) -> AiJobError:
    return AiJobError(code=code, kind=kind, retryable=kind == TRANSIENT)


@pytest.mark.asyncio
async def test_relays_the_turn_and_records_completion():
    uow = _uow()
    signals = AiJobCancelSignals()
    started, delta, completed = _started(), _delta(), _completed()
    orchestrator = FakeOrchestrator([started, delta, completed])
    runner = _runner(uow, signals)
    prepared = _prepared()
    session = runner.start(orchestrator=orchestrator, claim=_claim(), prepared=prepared)

    events = await _collect(runner, session)

    assert events == [
        ChatSseEvent("message.started", {**started.data, "ai_job_id": JOB_ID}),
        delta,
        ChatSseEvent(
            "message.completed",
            {**completed.data, "ai_job_id": JOB_ID, "ai_job_status": "completed"},
        ),
    ]
    # The orchestrator's own events are left as they were.
    assert "ai_job_id" not in started.data
    assert "ai_job_id" not in completed.data
    assert [(user_id, turn is prepared) for user_id, turn in orchestrator.streamed] == [
        (USER_ID, True)
    ]
    assert orchestrator.stream_closed
    uow.ai_jobs.complete.assert_awaited_once_with(
        _claim(),
        result={"thread_id": "t1", "assistant_message_id": "m-asst"},
        notify_delay_seconds=GRACE_SECONDS,
    )
    uow.ai_jobs.fail_attempt.assert_not_awaited()
    assert orchestrator.abandoned == []
    # The stream frees the provider slot itself; the runner never does.
    assert orchestrator.released == []
    # The cancel watch ends with the turn.
    assert signals.signal(JOB_ID) is False
    assert session.task.done() and not session.task.cancelled()


@pytest.mark.asyncio
async def test_stream_ends_when_the_turn_is_cancelled_before_it_starts():
    orchestrator = FakeOrchestrator([_started(), _completed()])
    runner = _runner(_uow())
    session = _start(runner, orchestrator)

    session.task.cancel()

    assert await _collect(runner, session) == []
    assert session.task.cancelled()
    assert orchestrator.streamed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "kind", "retry_delay", "status"),
    [
        ("CHAT_PROVIDER_UNAVAILABLE", TRANSIENT, FIRST_RETRY_DELAY, AiJobStatus.QUEUED),
        ("CHAT_DAILY_LIMIT", PERMANENT, None, AiJobStatus.FAILED),
    ],
)
async def test_error_terminal_records_a_failed_attempt(code, kind, retry_delay, status):
    uow = _uow()
    uow.ai_jobs.fail_attempt.return_value = status
    terminal = _turn_error(code)
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([_started(), terminal]))

    events = await _collect(runner, session)

    assert [event.event for event in events] == ["message.started", "message.error"]
    assert events[-1] == ChatSseEvent(
        "message.error",
        {**terminal.data, "ai_job_id": JOB_ID, "ai_job_status": status.value},
    )
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        _claim(),
        error=_failure(code, kind),
        retry_delay_seconds=retry_delay,
        notify_delay_seconds=GRACE_SECONDS,
    )
    uow.ai_jobs.complete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [{}, {"code": ""}, {"code": 503}],
    ids=["missing", "empty", "not-a-string"],
)
async def test_error_terminal_without_a_usable_code_reports_a_turn_failure(data):
    uow = _uow()
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([ChatSseEvent("message.error", data)]))

    events = await _collect(runner, session)

    # The client gets the code the job records, not the unusable one.
    assert events == [
        ChatSseEvent(
            "message.error",
            {
                "code": "CHAT_TURN_FAILED",
                "retryable": True,
                "ai_job_id": JOB_ID,
                "ai_job_status": "queued",
            },
        )
    ]
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        _claim(),
        error=_failure("CHAT_TURN_FAILED"),
        retry_delay_seconds=FIRST_RETRY_DELAY,
        notify_delay_seconds=GRACE_SECONDS,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lookup", "expected"),
    [
        (make_job(job_type=AiJobType.CHAT, status=AiJobStatus.RUNNING), "running"),
        (None, None),
        (RuntimeError("db down"), None),
    ],
    ids=["job-found", "job-missing", "lookup-fails"],
)
async def test_error_terminal_reports_current_status_when_attempt_lost_the_job(
    lookup, expected
):
    uow = _uow()
    uow.ai_jobs.fail_attempt.return_value = None
    if isinstance(lookup, Exception):
        uow.ai_jobs.get.side_effect = lookup
    else:
        uow.ai_jobs.get.return_value = lookup
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([_turn_error()]))

    events = await _collect(runner, session)

    assert events[-1].data["ai_job_status"] == expected
    uow.ai_jobs.get.assert_awaited_once_with(user_id=USER_ID, job_id=JOB_ID)


@pytest.mark.asyncio
async def test_stream_ending_without_terminal_event_fails_the_attempt():
    uow = _uow()
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([_started()]))

    events = await _collect(runner, session)

    assert events[-1] == _runner_error(
        "CHAT_TURN_FAILED", retryable=True, status="queued"
    )
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        _claim(),
        error=_failure("CHAT_TURN_FAILED"),
        retry_delay_seconds=FIRST_RETRY_DELAY,
        notify_delay_seconds=GRACE_SECONDS,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lookup", "expected"),
    [
        (make_job(job_type=AiJobType.CHAT, status=AiJobStatus.COMPLETED), "completed"),
        (RuntimeError("db down"), None),
    ],
    ids=["job-found", "lookup-fails"],
)
async def test_completion_reports_current_status_when_attempt_lost_the_job(
    lookup, expected
):
    uow = _uow()
    uow.ai_jobs.complete.return_value = False
    if isinstance(lookup, Exception):
        uow.ai_jobs.get.side_effect = lookup
    else:
        uow.ai_jobs.get.return_value = lookup
    completed = _completed()
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([completed]))

    events = await _collect(runner, session)

    assert events == [
        ChatSseEvent(
            "message.completed",
            {**completed.data, "ai_job_id": JOB_ID, "ai_job_status": expected},
        )
    ]


@pytest.mark.asyncio
async def test_turn_finishes_and_is_recorded_after_the_client_detaches():
    uow = _uow()
    gate = asyncio.Event()
    orchestrator = FakeOrchestrator([_started(), _completed()], gate=gate)
    runner = _runner(uow)
    session = _start(runner, orchestrator)
    stream = runner.stream(session)

    async with asyncio.timeout(2):
        first = await anext(stream)
        await stream.aclose()
        assert session.attached is False
        gate.set()
        await session.task

    assert first.event == "message.started"
    assert orchestrator.stream_closed
    uow.ai_jobs.complete.assert_awaited_once_with(
        _claim(),
        result={"thread_id": "t1", "assistant_message_id": "m-asst"},
        notify_delay_seconds=GRACE_SECONDS,
    )
    # Events after the detach are dropped; only the end marker is queued.
    assert session.queue.qsize() == 1
    assert session.queue.get_nowait() is None


@pytest.mark.asyncio
async def test_cancel_signal_stops_the_turn_and_cancels_the_job():
    uow = _uow()
    signals = AiJobCancelSignals()
    orchestrator = FakeOrchestrator([_started()], hang=True)
    runner = _runner(uow, signals)
    session = _start(runner, orchestrator)
    stream = runner.stream(session)

    async with asyncio.timeout(2):
        first = await anext(stream)
        assert signals.signal(JOB_ID) is True
        rest = [event async for event in stream]

    assert first.event == "message.started"
    assert rest == [_runner_error("CHAT_STOPPED", retryable=False, status="cancelled")]
    uow.ai_jobs.cancel_claimed.assert_awaited_once_with(_claim())
    uow.ai_jobs.fail_attempt.assert_not_awaited()
    uow.ai_jobs.complete.assert_not_awaited()
    assert orchestrator.abandoned == ["CHAT_STOPPED"]
    assert orchestrator.stream_closed
    assert signals.signal(JOB_ID) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owned", "current", "expected"),
    [
        (True, AiJobStatus.RUNNING, "cancelled"),
        (False, AiJobStatus.COMPLETED, "completed"),
    ],
    ids=["owned", "not-owned"],
)
async def test_cancel_seen_on_heartbeat_stops_the_turn(owned, current, expected):
    uow = _uow()
    uow.ai_jobs.heartbeat.return_value = AiJobHeartbeat(
        renewed=True, cancel_requested=True
    )
    uow.ai_jobs.cancel_claimed.return_value = owned
    uow.ai_jobs.get.return_value = make_job(job_type=AiJobType.CHAT, status=current)
    orchestrator = FakeOrchestrator([_started()], hang=True)
    runner = _runner(uow)
    session = _start(runner, orchestrator)

    events = await _collect(runner, session)

    assert events[-1] == _runner_error("CHAT_STOPPED", retryable=False, status=expected)
    uow.ai_jobs.heartbeat.assert_awaited_with(_claim(), lease_seconds=LEASE_SECONDS)
    uow.ai_jobs.cancel_claimed.assert_awaited_once_with(_claim())
    assert orchestrator.abandoned == ["CHAT_STOPPED"]


@pytest.mark.asyncio
async def test_deadline_interrupts_the_turn_and_records_a_timeout():
    uow = _uow()
    orchestrator = FakeOrchestrator([_started()], hang=True)
    runner = _runner(uow, deadline_seconds=0.05)
    session = _start(runner, orchestrator)

    events = await _collect(runner, session)

    assert events[-1] == _runner_error("AI_TIMEOUT", retryable=True, status="queued")
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        _claim(),
        error=_failure("AI_TIMEOUT"),
        retry_delay_seconds=FIRST_RETRY_DELAY,
        notify_delay_seconds=GRACE_SECONDS,
    )
    assert orchestrator.abandoned == ["CHAT_TURN_FAILED"]
    assert orchestrator.stream_closed


@pytest.mark.asyncio
async def test_lost_lease_interrupts_the_turn_without_writing(caplog):
    uow = _uow()
    uow.ai_jobs.heartbeat.return_value = AiJobHeartbeat(
        renewed=False, cancel_requested=False
    )
    orchestrator = FakeOrchestrator([_started()], hang=True)
    runner = _runner(uow)
    session = _start(runner, orchestrator)

    with caplog.at_level(logging.WARNING, logger="src.app.services.ai_jobs.recorder"):
        events = await _collect(runner, session)

    assert events[-1] == _runner_error(
        "CHAT_TURN_FAILED", retryable=True, status="running"
    )
    uow.ai_jobs.complete.assert_not_awaited()
    uow.ai_jobs.fail_attempt.assert_not_awaited()
    uow.ai_jobs.cancel_claimed.assert_not_awaited()
    assert orchestrator.abandoned == ["CHAT_TURN_FAILED"]
    assert "AI job lease lost mid-attempt" in caplog.messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "failure", "status"),
    [
        (RuntimeError("boom"), _failure("INTERNAL_ERROR"), AiJobStatus.QUEUED),
        (
            ChatRateLimitedError(60, daily=True),
            _failure("CHAT_DAILY_LIMIT", PERMANENT),
            AiJobStatus.FAILED,
        ),
    ],
    ids=["unexpected", "daily-limit"],
)
async def test_orchestrator_error_is_mapped_and_recorded(error, failure, status):
    uow = _uow()
    uow.ai_jobs.fail_attempt.return_value = status
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([_started()], error=error))

    events = await _collect(runner, session)

    assert events[-1] == _runner_error(
        failure.code, retryable=failure.retryable, status=status.value
    )
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        _claim(),
        error=failure,
        retry_delay_seconds=FIRST_RETRY_DELAY if failure.kind == TRANSIENT else None,
        notify_delay_seconds=GRACE_SECONDS,
    )


@pytest.mark.asyncio
async def test_turn_cancelled_from_inside_is_recorded_as_interrupted():
    uow = _uow()
    orchestrator = FakeOrchestrator([_started()], error=asyncio.CancelledError())
    runner = _runner(uow)
    session = _start(runner, orchestrator)

    events = await _collect(runner, session)

    assert events[-1] == _runner_error(
        "WORKER_INTERRUPTED", retryable=True, status="queued"
    )
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        _claim(),
        error=_failure("WORKER_INTERRUPTED"),
        retry_delay_seconds=FIRST_RETRY_DELAY,
        notify_delay_seconds=GRACE_SECONDS,
    )
    assert orchestrator.abandoned == ["CHAT_TURN_FAILED"]
    # Only the turn was cancelled; the runner itself carries on.
    assert session.task.done() and not session.task.cancelled()


@pytest.mark.asyncio
async def test_failure_to_record_ends_the_stream_with_unknown_status(caplog):
    uow = _uow()
    uow.ai_jobs.complete.side_effect = RuntimeError("db down")
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([_started(), _completed()]))

    with caplog.at_level(logging.ERROR, logger="src.app.services.ai_jobs.chat_runner"):
        events = await _collect(runner, session)

    assert [event.event for event in events] == ["message.started", "message.error"]
    assert events[-1] == _runner_error("CHAT_TURN_FAILED", retryable=True, status=None)
    # The job keeps its lease until it runs out; nothing else is written.
    uow.ai_jobs.fail_attempt.assert_not_awaited()
    assert "AI chat job runner failed" in caplog.messages
    assert session.task.done() and not session.task.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("grace_seconds", [0, 0.01])
async def test_aclose_hands_an_unfinished_turn_back_to_the_queue(grace_seconds):
    uow = _uow()
    orchestrator = FakeOrchestrator([_started()], hang=True)
    runner = _runner(uow)
    session = _start(runner, orchestrator)
    stream = runner.stream(session)

    async with asyncio.timeout(2):
        first = await anext(stream)
        await runner.aclose(grace_seconds=grace_seconds)
        rest = [event async for event in stream]
        await orchestrator.abandoned_event.wait()

    assert first.event == "message.started"
    assert rest == [_runner_error("WORKER_INTERRUPTED", retryable=True, status=None)]
    assert session.task.cancelled()
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        _claim(),
        error=_failure("WORKER_INTERRUPTED"),
        retry_delay_seconds=FIRST_RETRY_DELAY,
        notify_delay_seconds=GRACE_SECONDS,
    )
    uow.ai_jobs.complete.assert_not_awaited()
    assert orchestrator.abandoned == ["CHAT_TURN_FAILED"]
    assert orchestrator.released == []


@pytest.mark.asyncio
async def test_aclose_lets_a_turn_finish_within_the_grace():
    uow = _uow()
    gate = asyncio.Event()
    runner = _runner(uow)
    session = _start(runner, FakeOrchestrator([_started(), _completed()], gate=gate))
    asyncio.get_running_loop().call_later(0.01, gate.set)

    async with asyncio.timeout(2):
        await runner.aclose(grace_seconds=2.0)

    assert session.task.done() and not session.task.cancelled()
    uow.ai_jobs.complete.assert_awaited_once()
    uow.ai_jobs.fail_attempt.assert_not_awaited()
    events = await _collect(runner, session)
    assert [event.event for event in events] == ["message.started", "message.completed"]
    assert events[-1].data["ai_job_status"] == "completed"


@pytest.mark.asyncio
async def test_aclose_without_running_turns_returns_at_once():
    runner = _runner(_uow())

    async with asyncio.timeout(1):
        await runner.aclose(grace_seconds=5.0)

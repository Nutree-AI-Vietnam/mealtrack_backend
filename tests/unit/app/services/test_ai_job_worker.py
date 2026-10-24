"""AiJobWorker runs one attempt per claimed job and records how it ended."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from tests.fixtures.fakes.fake_ai_jobs import JOB_ID, FakeAiJobsUow, make_claim

from src.app.services.ai_jobs.executors import AiJobInterrupt
from src.app.services.ai_jobs.signals import AiJobCancelSignals, AiJobWakeup
from src.app.services.ai_jobs.worker import AiJobWorker
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import (
    AiJobErrorKind,
    AiJobHeartbeat,
    AiJobStatus,
    AiJobType,
)

LEASE_SECONDS = 120
GRACE_SECONDS = 8
GLOBAL_CAPACITY = 6
RESULT = {"items": [{"name": "egg"}]}
RENEWED = AiJobHeartbeat(renewed=True, cancel_requested=False)
LOST = AiJobHeartbeat(renewed=False, cancel_requested=False)
CANCEL_REQUESTED = AiJobHeartbeat(renewed=True, cancel_requested=True)
INTERRUPTIBLE = [AiJobType.PARSE_TEXT, AiJobType.CHAT]


class _Executor:
    """Runs ``body`` for each attempt and records what the attempt went through."""

    def __init__(self, body: Callable[[], Awaitable[Any]]) -> None:
        self._body = body
        self.started = asyncio.Event()
        self.cancelled = False
        # The interrupt code at the moment the attempt was cancelled.
        self.cancelled_with: str | None = None

    async def execute(self, claim, interrupt: AiJobInterrupt) -> Any:
        self.started.set()
        try:
            return await self._body()
        except asyncio.CancelledError:
            self.cancelled = True
            self.cancelled_with = interrupt.code
            raise


def _returns(value: Any) -> Callable[[], Awaitable[Any]]:
    async def body() -> Any:
        return value

    return body


def _raises(exc: BaseException) -> Callable[[], Awaitable[Any]]:
    async def body() -> Any:
        raise exc

    return body


def _blocks(release: asyncio.Event | None = None) -> Callable[[], Awaitable[Any]]:
    async def body() -> Any:
        await (release or asyncio.Event()).wait()
        return RESULT

    return body


@pytest.fixture(autouse=True)
def metrics():
    with (
        patch("src.app.services.ai_jobs.recorder.increment_metric") as attempts,
        patch("src.app.services.ai_jobs.worker.distribution_metric") as durations,
    ):
        yield SimpleNamespace(attempts=attempts, durations=durations)


@pytest.fixture
def uow() -> FakeAiJobsUow:
    uow = FakeAiJobsUow()
    uow.ai_jobs.claim_next.return_value = make_claim()
    uow.ai_jobs.heartbeat.return_value = RENEWED
    uow.ai_jobs.complete.return_value = True
    uow.ai_jobs.fail_attempt.return_value = AiJobStatus.QUEUED
    uow.ai_jobs.cancel_claimed.return_value = True
    return uow


def _worker(
    uow: FakeAiJobsUow,
    executors: dict[AiJobType, Any],
    *,
    signals: AiJobCancelSignals | None = None,
    wakeup: AiJobWakeup | None = None,
    heartbeat_seconds: float = 60.0,
    deadline_seconds: float = 60.0,
    poll_seconds: float = 0.01,
    ready: Callable[[], Awaitable[bool]] | None = None,
) -> AiJobWorker:
    return AiJobWorker(
        uow_factory=uow,
        executors=executors,
        cancel_signals=signals or AiJobCancelSignals(),
        wakeup=wakeup or AiJobWakeup(),
        concurrency=1,
        global_capacity=GLOBAL_CAPACITY,
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=heartbeat_seconds,
        deadline_seconds=deadline_seconds,
        poll_seconds=poll_seconds,
        notify_grace_seconds=GRACE_SECONDS,
        ready=ready,
        rng=lambda: 0.5,
    )


def _claim_type(uow: FakeAiJobsUow, job_type: AiJobType):
    claim = make_claim(job_type=job_type)
    uow.ai_jobs.claim_next.return_value = claim
    return claim


async def _start(
    worker: AiJobWorker, executor: _Executor, stop: asyncio.Event | None = None
) -> asyncio.Task[bool]:
    attempt = asyncio.create_task(worker.run_once(stop))
    await asyncio.wait_for(executor.started.wait(), timeout=1)
    return attempt


def _recorded_error(uow: FakeAiJobsUow):
    uow.ai_jobs.fail_attempt.assert_awaited_once()
    return uow.ai_jobs.fail_attempt.await_args.kwargs["error"]


def _assert_nothing_written(uow: FakeAiJobsUow) -> None:
    uow.ai_jobs.complete.assert_not_awaited()
    uow.ai_jobs.fail_attempt.assert_not_awaited()
    uow.ai_jobs.cancel_claimed.assert_not_awaited()


# -- one attempt -----------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_without_a_due_job_runs_nothing(uow):
    uow.ai_jobs.claim_next.return_value = None
    executor = _Executor(_returns(RESULT))

    assert await _worker(uow, {AiJobType.PARSE_TEXT: executor}).run_once() is False

    uow.ai_jobs.claim_next.assert_awaited_once_with(
        lease_seconds=LEASE_SECONDS, global_capacity=GLOBAL_CAPACITY
    )
    assert not executor.started.is_set()


@pytest.mark.asyncio
async def test_a_result_completes_the_job(uow, metrics):
    signals = AiJobCancelSignals()
    executor = _Executor(_returns(RESULT))
    worker = _worker(uow, {AiJobType.PARSE_TEXT: executor}, signals=signals)

    assert await worker.run_once() is True

    uow.ai_jobs.complete.assert_awaited_once_with(
        make_claim(), result=RESULT, notify_delay_seconds=GRACE_SECONDS
    )
    uow.ai_jobs.fail_attempt.assert_not_awaited()
    # The finished attempt no longer listens for cancel requests.
    assert signals.signal(JOB_ID) is False
    metrics.durations.assert_called_once()
    assert metrics.durations.call_args.kwargs["attributes"] == {
        "job_type": "parse_text"
    }


@pytest.mark.parametrize(
    ("exc", "code", "kind"),
    [
        (
            AiJobExecutionError("NOT_FOOD_TEXT"),
            "NOT_FOOD_TEXT",
            AiJobErrorKind.PERMANENT,
        ),
        (TimeoutError(), "AI_TIMEOUT", AiJobErrorKind.TRANSIENT),
    ],
)
@pytest.mark.asyncio
async def test_an_executor_error_is_recorded_with_its_code(uow, exc, code, kind):
    executor = _Executor(_raises(exc))

    assert await _worker(uow, {AiJobType.PARSE_TEXT: executor}).run_once() is True

    error = _recorded_error(uow)
    assert (error.code, error.kind) == (code, kind)
    uow.ai_jobs.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_result_that_is_not_a_dict_fails_as_an_internal_error(uow, caplog):
    executor = _Executor(_returns(["egg"]))

    with caplog.at_level(logging.ERROR, logger="src.app.services.ai_jobs.worker"):
        assert await _worker(uow, {AiJobType.PARSE_TEXT: executor}).run_once()

    assert _recorded_error(uow).code == "INTERNAL_ERROR"
    uow.ai_jobs.complete.assert_not_awaited()
    assert "returned list instead of a dict" in caplog.text


@pytest.mark.asyncio
async def test_a_job_type_without_an_executor_fails_as_an_internal_error(uow, metrics):
    signals = AiJobCancelSignals()

    assert await _worker(uow, {}, signals=signals).run_once() is True

    # Transient, so a worker that knows the type can pick it up on retry.
    error = _recorded_error(uow)
    assert (error.code, error.kind) == ("INTERNAL_ERROR", AiJobErrorKind.TRANSIENT)
    assert signals.signal(JOB_ID) is False
    metrics.durations.assert_called_once()


@pytest.mark.asyncio
async def test_work_cancelled_from_inside_fails_as_worker_interrupted(uow):
    executor = _Executor(_raises(asyncio.CancelledError()))

    assert await _worker(uow, {AiJobType.PARSE_TEXT: executor}).run_once() is True

    assert _recorded_error(uow).code == "WORKER_INTERRUPTED"
    uow.ai_jobs.complete.assert_not_awaited()


# -- attempts that end early -----------------------------------------------


@pytest.mark.parametrize("job_type", [*INTERRUPTIBLE, AiJobType.MEAL_SCAN])
@pytest.mark.asyncio
async def test_an_attempt_past_its_deadline_is_stopped_and_fails_as_a_timeout(
    uow, job_type
):
    _claim_type(uow, job_type)
    executor = _Executor(_blocks())
    worker = _worker(uow, {job_type: executor}, deadline_seconds=0.05)

    assert await asyncio.wait_for(worker.run_once(), timeout=2) is True

    assert executor.cancelled_with == "AI_TIMEOUT"
    error = _recorded_error(uow)
    assert (error.code, error.kind) == ("AI_TIMEOUT", AiJobErrorKind.TRANSIENT)
    uow.ai_jobs.complete.assert_not_awaited()


@pytest.mark.parametrize("via", ["signal", "heartbeat"])
@pytest.mark.parametrize("job_type", INTERRUPTIBLE)
@pytest.mark.asyncio
async def test_a_cancel_request_stops_an_interruptible_attempt(uow, job_type, via):
    claim = _claim_type(uow, job_type)
    if via == "heartbeat":
        # Requested through another process; seen on the next heartbeat.
        uow.ai_jobs.heartbeat.return_value = CANCEL_REQUESTED
    signals = AiJobCancelSignals()
    executor = _Executor(_blocks())
    worker = _worker(uow, {job_type: executor}, signals=signals, heartbeat_seconds=0.01)

    attempt = await _start(worker, executor)
    if via == "signal":
        assert signals.signal(JOB_ID) is True
    assert await asyncio.wait_for(attempt, timeout=2) is True

    assert executor.cancelled_with == "CHAT_STOPPED"
    uow.ai_jobs.cancel_claimed.assert_awaited_once_with(claim)
    uow.ai_jobs.complete.assert_not_awaited()
    uow.ai_jobs.fail_attempt.assert_not_awaited()


@pytest.mark.parametrize("via", ["signal", "heartbeat"])
@pytest.mark.asyncio
async def test_a_cancel_request_lets_a_meal_scan_finish_and_complete(uow, via):
    claim = _claim_type(uow, AiJobType.MEAL_SCAN)
    if via == "heartbeat":
        uow.ai_jobs.heartbeat.return_value = CANCEL_REQUESTED
    signals = AiJobCancelSignals()
    release = asyncio.Event()
    executor = _Executor(_blocks(release))
    worker = _worker(
        uow, {AiJobType.MEAL_SCAN: executor}, signals=signals, heartbeat_seconds=0.01
    )

    attempt = await _start(worker, executor)
    if via == "signal":
        assert signals.signal(JOB_ID) is True
    # Several heartbeats pass while the scan keeps running.
    await asyncio.sleep(0.05)
    assert not attempt.done()
    release.set()
    assert await asyncio.wait_for(attempt, timeout=2) is True

    assert not executor.cancelled
    assert uow.ai_jobs.heartbeat.await_count >= 1
    # The repository withholds the push because a cancel was requested.
    uow.ai_jobs.complete.assert_awaited_once_with(
        claim, result=RESULT, notify_delay_seconds=GRACE_SECONDS
    )
    uow.ai_jobs.cancel_claimed.assert_not_awaited()


@pytest.mark.parametrize("job_type", INTERRUPTIBLE)
@pytest.mark.asyncio
async def test_shutdown_stops_an_interruptible_attempt_and_requeues_it(uow, job_type):
    _claim_type(uow, job_type)
    stop = asyncio.Event()
    executor = _Executor(_blocks())
    worker = _worker(uow, {job_type: executor})

    attempt = await _start(worker, executor, stop)
    stop.set()
    assert await asyncio.wait_for(attempt, timeout=2) is True

    assert executor.cancelled_with == "WORKER_INTERRUPTED"
    error = _recorded_error(uow)
    assert (error.code, error.kind) == (
        "WORKER_INTERRUPTED",
        AiJobErrorKind.TRANSIENT,
    )
    # Attempts remain, so the job goes back to the queue.
    assert uow.ai_jobs.fail_attempt.await_args.kwargs["retry_delay_seconds"]


@pytest.mark.asyncio
async def test_shutdown_lets_a_meal_scan_finish(uow):
    claim = _claim_type(uow, AiJobType.MEAL_SCAN)
    stop = asyncio.Event()
    release = asyncio.Event()
    executor = _Executor(_blocks(release))
    worker = _worker(uow, {AiJobType.MEAL_SCAN: executor})

    attempt = await _start(worker, executor, stop)
    stop.set()
    await asyncio.sleep(0.02)
    assert not attempt.done()
    release.set()
    assert await asyncio.wait_for(attempt, timeout=2) is True

    assert not executor.cancelled
    uow.ai_jobs.complete.assert_awaited_once_with(
        claim, result=RESULT, notify_delay_seconds=GRACE_SECONDS
    )


@pytest.mark.parametrize("job_type", [AiJobType.PARSE_TEXT, AiJobType.MEAL_SCAN])
@pytest.mark.asyncio
async def test_a_lost_lease_stops_the_attempt_without_writing(uow, metrics, job_type):
    _claim_type(uow, job_type)
    uow.ai_jobs.heartbeat.return_value = LOST
    executor = _Executor(_blocks())
    worker = _worker(uow, {job_type: executor}, heartbeat_seconds=0.01)

    assert await asyncio.wait_for(worker.run_once(), timeout=2) is True

    assert executor.cancelled
    assert executor.cancelled_with is None
    _assert_nothing_written(uow)
    metrics.attempts.assert_called_once_with(
        "ai_jobs.attempt",
        attributes={"job_type": job_type.value, "outcome": "lost_lease"},
    )


@pytest.mark.asyncio
async def test_hard_shutdown_hands_the_job_back_then_cancels(uow, metrics):
    _claim_type(uow, AiJobType.MEAL_SCAN)
    signals = AiJobCancelSignals()
    executor = _Executor(_blocks())
    worker = _worker(uow, {AiJobType.MEAL_SCAN: executor}, signals=signals)

    attempt = await _start(worker, executor)
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    await asyncio.sleep(0)

    assert executor.cancelled_with == "WORKER_INTERRUPTED"
    assert _recorded_error(uow).code == "WORKER_INTERRUPTED"
    uow.ai_jobs.complete.assert_not_awaited()
    assert signals.signal(JOB_ID) is False
    metrics.durations.assert_called_once()


@pytest.mark.asyncio
async def test_a_supervision_failure_cancels_the_work_and_propagates(uow):
    executor = _Executor(_blocks())

    async def broken_supervision(**_):
        await executor.started.wait()
        raise RuntimeError("supervision failed")

    worker = _worker(uow, {AiJobType.PARSE_TEXT: executor})
    with patch("src.app.services.ai_jobs.worker.supervise_attempt", broken_supervision):
        with pytest.raises(RuntimeError, match="supervision failed"):
            await worker.run_once()
    await asyncio.sleep(0)

    assert executor.cancelled
    # The lease runs out and the job is claimed again.
    _assert_nothing_written(uow)


# -- lanes -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_at_once_when_already_stopped(uow):
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(_worker(uow, {}).run(stop), timeout=1)

    uow.ai_jobs.claim_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lane_logs_a_failed_claim_and_keeps_going(uow, caplog):
    stop = asyncio.Event()
    calls = 0

    async def claim_next(**_):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database unavailable")
        stop.set()
        return None

    uow.ai_jobs.claim_next.side_effect = claim_next

    with caplog.at_level(logging.ERROR, logger="src.app.services.ai_jobs.worker"):
        await asyncio.wait_for(_worker(uow, {}).run(stop), timeout=2)

    assert calls == 2
    assert "AI job lane 0 failed to claim or run a job" in caplog.text


@pytest.mark.asyncio
async def test_a_lane_does_not_claim_until_the_schema_is_ready(uow):
    stop = asyncio.Event()
    checks = 0

    async def ready() -> bool:
        nonlocal checks
        checks += 1
        if checks == 2:
            stop.set()
        return False

    await asyncio.wait_for(_worker(uow, {}, ready=ready).run(stop), timeout=2)

    assert checks == 2
    uow.ai_jobs.claim_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_enqueue_wakes_an_idle_lane_at_once(uow):
    stop = asyncio.Event()
    wakeup = AiJobWakeup()
    claims: asyncio.Queue[None] = asyncio.Queue()

    async def claim_next(**_):
        claims.put_nowait(None)
        return None

    uow.ai_jobs.claim_next.side_effect = claim_next
    # A long poll: only the wakeup can bring the second claim within a second.
    lanes = asyncio.create_task(
        _worker(uow, {}, wakeup=wakeup, poll_seconds=30).run(stop)
    )

    await asyncio.wait_for(claims.get(), timeout=1)
    await asyncio.sleep(0.01)
    wakeup.notify()
    await asyncio.wait_for(claims.get(), timeout=1)

    stop.set()
    wakeup.notify()
    await asyncio.wait_for(lanes, timeout=1)


@pytest.mark.asyncio
async def test_a_lane_claims_again_right_after_finishing_a_job(uow):
    stop = asyncio.Event()
    wakeup = AiJobWakeup()
    executor = _Executor(_returns(RESULT))
    claims = [make_claim(), None]

    async def claim_next(**_):
        claim = claims.pop(0)
        if claim is None:
            # Shut down the way the app does, so the idle lane wakes.
            stop.set()
            wakeup.notify()
        return claim

    uow.ai_jobs.claim_next.side_effect = claim_next
    # A long poll: waiting between the two claims would time the test out.
    worker = _worker(
        uow, {AiJobType.PARSE_TEXT: executor}, wakeup=wakeup, poll_seconds=30
    )

    await asyncio.wait_for(worker.run(stop), timeout=1)

    assert claims == []
    uow.ai_jobs.complete.assert_awaited_once()

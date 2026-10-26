"""AiJobAttemptRecorder writes one attempt's outcome, fenced on its claim."""

import asyncio
import logging
from unittest.mock import patch

import pytest
from tests.fixtures.fakes.fake_ai_jobs import (
    FakeAiJobsUow,
    make_claim,
    make_error,
    make_job,
)

from src.app.services.ai_jobs.recorder import AiJobAttemptRecorder, count_attempt
from src.domain.model.ai_jobs import (
    AiJobErrorKind,
    AiJobHeartbeat,
    AiJobStatus,
    AiJobType,
)

LEASE_SECONDS = 120
GRACE_SECONDS = 8


@pytest.fixture
def metrics():
    with patch("src.app.services.ai_jobs.recorder.increment_metric") as metric:
        yield metric


def _recorder(
    uow: FakeAiJobsUow, *, rng=lambda: 0.5, heartbeat_seconds: float = 2.0
) -> AiJobAttemptRecorder:
    return AiJobAttemptRecorder(
        uow_factory=uow,
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=heartbeat_seconds,
        notify_grace_seconds=GRACE_SECONDS,
        rng=rng,
    )


def _attempt_metric(outcome: str, error_code: str | None = None, job_type="parse_text"):
    attributes = {"job_type": job_type, "outcome": outcome}
    if error_code:
        attributes["error_code"] = error_code
    return ("ai_jobs.attempt",), {"attributes": attributes}


# --- heartbeat ---


@pytest.mark.asyncio
async def test_heartbeat_extends_the_lease_and_returns_the_answer():
    uow = FakeAiJobsUow()
    beat = AiJobHeartbeat(renewed=True, cancel_requested=True)
    uow.ai_jobs.heartbeat.return_value = beat
    claim = make_claim()

    assert await _recorder(uow).heartbeat(claim) == beat

    uow.ai_jobs.heartbeat.assert_awaited_once_with(claim, lease_seconds=LEASE_SECONDS)


@pytest.mark.asyncio
async def test_heartbeat_returns_none_when_the_database_fails(caplog):
    uow = FakeAiJobsUow()
    uow.ai_jobs.heartbeat.side_effect = RuntimeError("database is down")

    with caplog.at_level(logging.WARNING):
        assert await _recorder(uow).heartbeat(make_claim()) is None

    assert uow.rolled_back == 1
    assert "AI job heartbeat failed" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_returns_none_when_the_database_hangs():
    uow = FakeAiJobsUow()

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    uow.ai_jobs.heartbeat.side_effect = hang
    recorder = _recorder(uow)
    # The real floor is 5 s; shrink it so the test stays fast.
    recorder._heartbeat_timeout = 0.01

    assert await recorder.heartbeat(make_claim()) is None


@pytest.mark.parametrize(
    ("heartbeat_seconds", "timeout"), [(0.5, 5.0), (2.0, 5.0), (4.0, 8.0)]
)
def test_heartbeat_timeout_is_two_beats_but_at_least_five_seconds(
    heartbeat_seconds, timeout
):
    recorder = _recorder(FakeAiJobsUow(), heartbeat_seconds=heartbeat_seconds)

    assert recorder._heartbeat_timeout == timeout


# --- complete ---


@pytest.mark.asyncio
async def test_complete_stores_the_result_after_the_notify_grace(metrics):
    uow = FakeAiJobsUow()
    uow.ai_jobs.complete.return_value = True
    claim = make_claim()

    assert await _recorder(uow).complete(claim, {"items": []}) is True

    uow.ai_jobs.complete.assert_awaited_once_with(
        claim, result={"items": []}, notify_delay_seconds=GRACE_SECONDS
    )
    assert uow.committed == 1
    assert metrics.call_args_list == [_attempt_metric("completed")]


@pytest.mark.asyncio
async def test_complete_reports_a_lost_lease(metrics):
    uow = FakeAiJobsUow()
    uow.ai_jobs.complete.return_value = False

    assert await _recorder(uow).complete(make_claim(), {"items": []}) is False

    assert metrics.call_args_list == [_attempt_metric("lost_lease")]


# --- fail ---


@pytest.mark.parametrize(
    ("attempts", "rng_value", "delay"),
    [
        (1, 0.5, 5.0),  # base 5 s, no jitter
        (2, 0.75, 11.0),  # 10 s, +10% jitter
        (3, 0.0, 16.0),  # 20 s, -20% jitter
    ],
)
@pytest.mark.asyncio
async def test_fail_requeues_a_transient_error_with_backoff(
    metrics, attempts, rng_value, delay
):
    uow = FakeAiJobsUow()
    uow.ai_jobs.fail_attempt.return_value = AiJobStatus.QUEUED
    claim = make_claim(attempts=attempts, max_attempts=4)
    error = make_error("AI_UNAVAILABLE")

    status = await _recorder(uow, rng=lambda: rng_value).fail(claim, error)

    assert status == AiJobStatus.QUEUED
    kwargs = uow.ai_jobs.fail_attempt.await_args.kwargs
    assert kwargs["error"] == error
    assert kwargs["retry_delay_seconds"] == pytest.approx(delay)
    assert kwargs["notify_delay_seconds"] == GRACE_SECONDS
    assert metrics.call_args_list == [_attempt_metric("queued", "AI_UNAVAILABLE")]


@pytest.mark.asyncio
async def test_fail_does_not_retry_a_permanent_error(metrics):
    uow = FakeAiJobsUow()
    uow.ai_jobs.fail_attempt.return_value = AiJobStatus.FAILED
    claim = make_claim(attempts=1)
    error = make_error("NOT_FOOD_IMAGE", kind=AiJobErrorKind.PERMANENT, retryable=False)

    status = await _recorder(uow).fail(claim, error)

    assert status == AiJobStatus.FAILED
    uow.ai_jobs.fail_attempt.assert_awaited_once_with(
        claim,
        error=error,
        retry_delay_seconds=None,
        notify_delay_seconds=GRACE_SECONDS,
    )
    assert metrics.call_args_list == [_attempt_metric("failed", "NOT_FOOD_IMAGE")]


@pytest.mark.asyncio
async def test_fail_does_not_retry_after_the_last_attempt(metrics):
    uow = FakeAiJobsUow()
    uow.ai_jobs.fail_attempt.return_value = AiJobStatus.FAILED
    claim = make_claim(attempts=4, max_attempts=4)

    await _recorder(uow).fail(claim, make_error("AI_UNAVAILABLE"))

    assert uow.ai_jobs.fail_attempt.await_args.kwargs["retry_delay_seconds"] is None


@pytest.mark.asyncio
async def test_fail_reports_a_lost_lease(metrics, caplog):
    uow = FakeAiJobsUow()
    uow.ai_jobs.fail_attempt.return_value = None

    with caplog.at_level(logging.WARNING):
        status = await _recorder(uow).fail(make_claim(), make_error("AI_TIMEOUT"))

    assert status is None
    assert metrics.call_args_list == [_attempt_metric("lost_lease", "AI_TIMEOUT")]
    record = next(r for r in caplog.records if r.message == "AI job attempt failed")
    assert record.error_code == "AI_TIMEOUT"
    assert record.next_status is None


# --- cancel ---


@pytest.mark.parametrize(
    ("owned", "outcome"), [(True, "cancelled"), (False, "lost_lease")]
)
@pytest.mark.asyncio
async def test_cancel_finishes_the_claimed_attempt(metrics, owned, outcome):
    uow = FakeAiJobsUow()
    uow.ai_jobs.cancel_claimed.return_value = owned
    claim = make_claim(job_type=AiJobType.CHAT)

    assert await _recorder(uow).cancel(claim) is owned

    uow.ai_jobs.cancel_claimed.assert_awaited_once_with(claim)
    assert metrics.call_args_list == [_attempt_metric(outcome, job_type="chat")]


# --- current_status ---


@pytest.mark.asyncio
async def test_current_status_reads_the_job():
    uow = FakeAiJobsUow()
    uow.ai_jobs.get.return_value = make_job(status=AiJobStatus.RUNNING)
    claim = make_claim()

    assert await _recorder(uow).current_status(claim) == AiJobStatus.RUNNING

    uow.ai_jobs.get.assert_awaited_once_with(user_id=claim.user_id, job_id=claim.id)


@pytest.mark.asyncio
async def test_current_status_is_none_for_a_missing_job():
    uow = FakeAiJobsUow()
    uow.ai_jobs.get.return_value = None

    assert await _recorder(uow).current_status(make_claim()) is None


@pytest.mark.asyncio
async def test_current_status_is_none_when_the_database_fails(caplog):
    uow = FakeAiJobsUow()
    uow.ai_jobs.get.side_effect = RuntimeError("database is down")

    with caplog.at_level(logging.WARNING):
        assert await _recorder(uow).current_status(make_claim()) is None

    assert "AI job status lookup failed" in caplog.text


# --- metrics ---


def test_lease_lost_logs_and_counts(metrics, caplog):
    with caplog.at_level(logging.WARNING):
        AiJobAttemptRecorder.lease_lost(make_claim(job_type=AiJobType.MEAL_SCAN))

    assert "AI job lease lost mid-attempt" in caplog.text
    assert metrics.call_args_list == [
        _attempt_metric("lost_lease", job_type="meal_scan")
    ]


def test_count_attempt_adds_the_error_code_only_when_given(metrics):
    claim = make_claim()

    count_attempt(claim, "completed")
    count_attempt(claim, "failed", error_code="AI_TIMEOUT")

    assert metrics.call_args_list == [
        _attempt_metric("completed"),
        _attempt_metric("failed", "AI_TIMEOUT"),
    ]

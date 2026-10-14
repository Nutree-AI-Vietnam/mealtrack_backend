"""In-process cancel signals, worker wakeups, and the schema probe."""

import asyncio

import pytest
from tests.fixtures.fakes.fake_ai_jobs import FakeAiJobsUow

from src.app.services.ai_jobs.signals import (
    AiJobCancelSignals,
    AiJobsSchemaProbe,
    AiJobWakeup,
)


async def _settle() -> None:
    """Let freshly created tasks run up to their first await."""
    for _ in range(3):
        await asyncio.sleep(0)


class _Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# --- AiJobCancelSignals ----------------------------------------------------


def test_signal_sets_the_watched_event():
    signals = AiJobCancelSignals()
    event = signals.watch("job-1")

    assert not event.is_set()
    assert signals.signal("job-1") is True
    assert event.is_set()


def test_signal_for_a_job_not_running_here_reports_false():
    assert AiJobCancelSignals().signal("job-1") is False


def test_signal_reaches_only_its_own_job():
    signals = AiJobCancelSignals()
    first = signals.watch("job-1")
    second = signals.watch("job-2")

    signals.signal("job-1")

    assert first.is_set()
    assert not second.is_set()


def test_released_jobs_no_longer_receive_signals():
    signals = AiJobCancelSignals()
    event = signals.watch("job-1")

    signals.release("job-1", event)

    assert signals.signal("job-1") is False
    assert not event.is_set()


def test_a_stale_release_keeps_the_newer_attempt_watched():
    signals = AiJobCancelSignals()
    old = signals.watch("job-1")
    new = signals.watch("job-1")

    signals.release("job-1", old)

    assert signals.signal("job-1") is True
    assert new.is_set()
    assert not old.is_set()


def test_releasing_an_unknown_job_is_a_no_op():
    signals = AiJobCancelSignals()

    signals.release("job-1", asyncio.Event())

    assert signals.signal("job-1") is False


# --- AiJobWakeup -----------------------------------------------------------


def test_each_notify_bumps_the_generation():
    wakeup = AiJobWakeup()

    assert wakeup.generation == 0
    wakeup.notify()
    wakeup.notify()
    assert wakeup.generation == 2


@pytest.mark.asyncio
async def test_notify_wakes_a_waiting_lane():
    wakeup = AiJobWakeup()
    waiter = asyncio.create_task(wakeup.wait(5.0))
    await _settle()

    wakeup.notify()

    assert await asyncio.wait_for(waiter, 0.5) is True


@pytest.mark.asyncio
async def test_notify_wakes_every_waiting_lane():
    wakeup = AiJobWakeup()
    waiters = [asyncio.create_task(wakeup.wait(5.0)) for _ in range(3)]
    await _settle()

    wakeup.notify()

    assert await asyncio.wait_for(asyncio.gather(*waiters), 0.5) == [True] * 3


@pytest.mark.asyncio
async def test_wait_returns_false_when_the_timeout_passes():
    wakeup = AiJobWakeup()

    assert await wakeup.wait(0.01) is False


@pytest.mark.asyncio
async def test_a_notify_before_the_wait_is_missed_without_a_generation():
    wakeup = AiJobWakeup()
    wakeup.notify()

    assert await wakeup.wait(0.01) is False


@pytest.mark.asyncio
async def test_a_notify_after_the_claim_attempt_is_never_slept_through():
    wakeup = AiJobWakeup()
    seen = wakeup.generation
    wakeup.notify()

    assert await asyncio.wait_for(wakeup.wait(5.0, since=seen), 0.5) is True


@pytest.mark.asyncio
async def test_a_current_generation_still_waits_for_the_next_notify():
    wakeup = AiJobWakeup()
    wakeup.notify()

    assert await wakeup.wait(0.01, since=wakeup.generation) is False


@pytest.mark.asyncio
async def test_finished_waits_leave_no_waiters_behind():
    wakeup = AiJobWakeup()
    await wakeup.wait(0.01)
    woken = asyncio.create_task(wakeup.wait(5.0))
    await _settle()
    wakeup.notify()
    await woken

    assert not wakeup._waiters


@pytest.mark.asyncio
async def test_a_cancelled_waiter_is_dropped_and_later_notifies_still_work():
    wakeup = AiJobWakeup()
    waiter = asyncio.create_task(wakeup.wait(5.0))
    await _settle()

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert not wakeup._waiters
    wakeup.notify()
    assert wakeup.generation == 1


# --- AiJobsSchemaProbe -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_migrated_schema_is_remembered_for_good():
    uow = FakeAiJobsUow()
    uow.ai_jobs.schema_ready.return_value = True
    clock = _Clock()
    probe = AiJobsSchemaProbe(uow, clock=clock)

    assert await probe.ready() is True
    uow.ai_jobs.schema_ready.return_value = False
    clock.now += 10_000
    assert await probe.ready() is True
    assert uow.ai_jobs.schema_ready.await_count == 1


@pytest.mark.asyncio
async def test_a_missing_schema_is_checked_again_after_retry_after():
    uow = FakeAiJobsUow()
    uow.ai_jobs.schema_ready.return_value = False
    clock = _Clock()
    probe = AiJobsSchemaProbe(
        uow, retry_after_seconds=60.0, error_retry_after_seconds=5.0, clock=clock
    )

    assert await probe.ready() is False
    clock.now += 59.9
    assert await probe.ready() is False
    assert uow.ai_jobs.schema_ready.await_count == 1

    uow.ai_jobs.schema_ready.return_value = True
    clock.now = 160.0
    assert await probe.ready() is True
    assert await probe.ready() is True
    assert uow.ai_jobs.schema_ready.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_check_is_retried_sooner_and_not_cached():
    uow = FakeAiJobsUow()
    uow.ai_jobs.schema_ready.side_effect = [RuntimeError("db down"), True]
    clock = _Clock()
    probe = AiJobsSchemaProbe(
        uow, retry_after_seconds=60.0, error_retry_after_seconds=5.0, clock=clock
    )

    assert await probe.ready() is False
    clock.now += 4.9
    assert await probe.ready() is False
    assert uow.ai_jobs.schema_ready.await_count == 1

    clock.now = 105.0
    assert await probe.ready() is True
    assert uow.ai_jobs.schema_ready.await_count == 2


@pytest.mark.asyncio
async def test_failing_to_open_a_unit_of_work_counts_as_a_failed_check():
    clock = _Clock()
    opened = 0

    def failing_factory():
        nonlocal opened
        opened += 1
        raise ConnectionError("database unreachable")

    probe = AiJobsSchemaProbe(failing_factory, clock=clock)

    assert await probe.ready() is False
    assert await probe.ready() is False
    assert opened == 1
    clock.now += 5.0
    assert await probe.ready() is False
    assert opened == 2


@pytest.mark.asyncio
async def test_probe_defaults_recheck_after_a_minute_or_five_seconds_after_errors():
    uow = FakeAiJobsUow()
    uow.ai_jobs.schema_ready.side_effect = [False, RuntimeError("db down"), True]
    clock = _Clock()
    probe = AiJobsSchemaProbe(uow, clock=clock)

    assert await probe.ready() is False
    clock.now = 159.9
    assert await probe.ready() is False
    clock.now = 160.0
    assert await probe.ready() is False
    clock.now = 164.9
    assert await probe.ready() is False
    clock.now = 165.0
    assert await probe.ready() is True
    assert uow.ai_jobs.schema_ready.await_count == 3

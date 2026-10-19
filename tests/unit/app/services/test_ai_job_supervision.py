"""supervise_attempt waits for one attempt while keeping its lease alive."""

import asyncio
import logging
import time

import pytest

from src.app.services.ai_jobs import supervision
from src.app.services.ai_jobs.supervision import (
    AttemptOutcome,
    interrupt_work,
    run_detached,
    supervise_attempt,
)
from src.domain.model.ai_jobs import AiJobHeartbeat

RENEWED = AiJobHeartbeat(renewed=True, cancel_requested=False)
LOST = AiJobHeartbeat(renewed=False, cancel_requested=False)
CANCEL_REQUESTED = AiJobHeartbeat(renewed=True, cancel_requested=True)
RESULT = {"items": []}


class _Heart:
    """Heartbeat stub that answers ``answer`` and can finish ``work`` on a beat."""

    def __init__(self, answer=RENEWED, *, work=None, finish_on=None, on_beat=None):
        self.answer = answer
        self.calls = 0
        self._work = work
        self._finish_on = finish_on
        self._on_beat = on_beat

    async def __call__(self):
        self.calls += 1
        if self._on_beat is not None:
            self._on_beat()
        if self._work is not None and self.calls == self._finish_on:
            self._work.set_result(RESULT)
        return self.answer


class _Clock:
    """Monotonic time that a test can move forward."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.monotonic() + self.offset


def _pending():
    return asyncio.get_running_loop().create_future()


def _finished():
    work = _pending()
    work.set_result(RESULT)
    return work


def _set():
    event = asyncio.Event()
    event.set()
    return event


async def _supervise(
    work,
    heartbeat,
    *,
    cancel_event=None,
    stop=None,
    interruptible=True,
    heartbeat_seconds=0.01,
    deadline=None,
    clock=time.monotonic,
):
    if cancel_event is None:
        cancel_event = asyncio.Event()
    if deadline is None:
        deadline = clock() + 60
    return await asyncio.wait_for(
        supervise_attempt(
            work=work,
            heartbeat=heartbeat,
            cancel_event=cancel_event,
            stop=stop,
            interruptible=interruptible,
            heartbeat_seconds=heartbeat_seconds,
            deadline=deadline,
            clock=clock,
        ),
        2,
    )


# --- supervise_attempt ---


@pytest.mark.asyncio
async def test_finished_work_needs_no_heartbeat():
    heart = _Heart()

    outcome = await _supervise(_finished(), heart, heartbeat_seconds=10)

    assert outcome is AttemptOutcome.FINISHED
    assert heart.calls == 0


@pytest.mark.asyncio
async def test_heartbeats_keep_the_lease_until_the_work_finishes():
    work = _pending()
    heart = _Heart(work=work, finish_on=3)

    assert await _supervise(work, heart) is AttemptOutcome.FINISHED
    assert heart.calls == 3


@pytest.mark.asyncio
async def test_a_lost_lease_ends_the_wait():
    work = _pending()
    heart = _Heart(LOST)

    assert await _supervise(work, heart) is AttemptOutcome.LOST_LEASE
    assert heart.calls == 1
    assert not work.done()
    work.cancel()


@pytest.mark.asyncio
async def test_an_unanswered_heartbeat_is_ignored():
    work = _pending()
    heart = _Heart(None, work=work, finish_on=3)

    assert await _supervise(work, heart) is AttemptOutcome.FINISHED
    assert heart.calls == 3


@pytest.mark.asyncio
async def test_a_cancel_signal_stops_interruptible_work_at_once():
    work = _pending()
    heart = _Heart()

    outcome = await _supervise(work, heart, cancel_event=_set(), heartbeat_seconds=10)

    assert outcome is AttemptOutcome.CANCELLED
    assert heart.calls == 0
    assert not work.done()
    work.cancel()


@pytest.mark.asyncio
async def test_a_cancel_seen_on_the_heartbeat_stops_interruptible_work():
    work = _pending()
    heart = _Heart(CANCEL_REQUESTED)

    assert await _supervise(work, heart) is AttemptOutcome.CANCELLED
    assert heart.calls == 1
    work.cancel()


@pytest.mark.asyncio
async def test_shutdown_stops_interruptible_work():
    work = _pending()

    outcome = await _supervise(work, _Heart(), stop=_set(), heartbeat_seconds=10)

    assert outcome is AttemptOutcome.STOPPED
    work.cancel()


@pytest.mark.asyncio
async def test_a_cancel_wins_over_shutdown():
    work = _pending()

    outcome = await _supervise(
        work, _Heart(), cancel_event=_set(), stop=_set(), heartbeat_seconds=10
    )

    assert outcome is AttemptOutcome.CANCELLED
    work.cancel()


@pytest.mark.asyncio
async def test_work_that_is_not_interruptible_ignores_cancel_and_shutdown():
    work = _pending()
    heart = _Heart(CANCEL_REQUESTED, work=work, finish_on=2)

    outcome = await _supervise(
        work, heart, cancel_event=_set(), stop=_set(), interruptible=False
    )

    assert outcome is AttemptOutcome.FINISHED
    assert heart.calls == 2


@pytest.mark.asyncio
async def test_a_finished_result_wins_over_a_cancel():
    outcome = await _supervise(
        _finished(), _Heart(), cancel_event=_set(), heartbeat_seconds=10
    )

    assert outcome is AttemptOutcome.FINISHED


# --- deadline ---


@pytest.mark.asyncio
async def test_the_deadline_ends_the_wait_between_heartbeats():
    work = _pending()
    heart = _Heart()

    outcome = await _supervise(
        work, heart, heartbeat_seconds=10, deadline=time.monotonic() + 0.05
    )

    assert outcome is AttemptOutcome.TIMED_OUT
    assert heart.calls == 0
    assert not work.done()
    work.cancel()


@pytest.mark.asyncio
async def test_a_deadline_that_passes_during_a_heartbeat_ends_the_wait():
    clock = _Clock()
    work = _pending()

    def slow_beat():
        clock.offset += 100

    heart = _Heart(on_beat=slow_beat)

    outcome = await _supervise(work, heart, deadline=clock() + 60, clock=clock)

    assert outcome is AttemptOutcome.TIMED_OUT
    assert heart.calls == 1
    assert not work.done()
    work.cancel()


@pytest.mark.asyncio
async def test_a_deadline_already_passed_ends_the_wait_at_once():
    work = _pending()
    heart = _Heart()

    outcome = await _supervise(work, heart, deadline=time.monotonic() - 1)

    assert outcome is AttemptOutcome.TIMED_OUT
    assert heart.calls == 0
    work.cancel()


@pytest.mark.asyncio
async def test_work_that_finishes_during_the_last_heartbeat_is_not_timed_out():
    clock = _Clock()
    work = _pending()

    def finish_then_pass_the_deadline():
        work.set_result(RESULT)
        clock.offset += 100

    heart = _Heart(on_beat=finish_then_pass_the_deadline)

    outcome = await _supervise(work, heart, deadline=clock() + 60, clock=clock)

    assert outcome is AttemptOutcome.FINISHED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer", [LOST, CANCEL_REQUESTED], ids=["lost-lease", "cancel-requested"]
)
async def test_work_that_finishes_during_a_heartbeat_wins_over_its_answer(answer):
    work = _pending()
    heart = _Heart(answer, work=work, finish_on=1)

    # The fenced write of the result decides whether it is kept.
    assert await _supervise(work, heart) is AttemptOutcome.FINISHED
    assert heart.calls == 1


@pytest.mark.asyncio
async def test_supervision_leaves_no_waiters_behind():
    before = asyncio.all_tasks()
    work = _pending()

    cancelled = await _supervise(
        work,
        _Heart(),
        cancel_event=_set(),
        stop=asyncio.Event(),
        heartbeat_seconds=10,
    )
    finished = await _supervise(
        _finished(), _Heart(), stop=asyncio.Event(), heartbeat_seconds=10
    )
    await asyncio.sleep(0)

    assert (cancelled, finished) == (AttemptOutcome.CANCELLED, AttemptOutcome.FINISHED)
    assert asyncio.all_tasks() - before == set()
    work.cancel()


# --- interrupt_work ---


@pytest.mark.asyncio
async def test_interrupt_work_leaves_finished_work_alone():
    work = _finished()

    await interrupt_work(work, timeout=2)

    assert work.result() == RESULT


@pytest.mark.asyncio
async def test_interrupt_work_cancels_and_waits_for_the_cleanup():
    started = asyncio.Event()
    cleaned_up = asyncio.Event()

    async def attempt():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.01)
            cleaned_up.set()
            raise

    work = asyncio.ensure_future(attempt())
    await started.wait()

    await interrupt_work(work, timeout=2)

    assert cleaned_up.is_set()
    assert work.cancelled()


@pytest.mark.asyncio
async def test_interrupt_work_stops_waiting_for_a_hung_cleanup():
    started = asyncio.Event()
    release = asyncio.Event()

    async def attempt():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise

    work = asyncio.ensure_future(attempt())
    await started.wait()

    await interrupt_work(work, timeout=0.01)

    assert not work.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await work


# --- run_detached ---


@pytest.mark.asyncio
async def test_run_detached_waits_for_the_write():
    written = []

    async def write():
        await asyncio.sleep(0)
        written.append("done")

    await run_detached(write(), timeout=2, description="record")

    assert written == ["done"]


@pytest.mark.asyncio
async def test_run_detached_logs_a_failed_write_instead_of_raising(caplog):
    async def write():
        raise RuntimeError("database is down")

    with caplog.at_level(logging.WARNING):
        await run_detached(write(), timeout=2, description="record interrupted attempt")

    record = next(
        r
        for r in caplog.records
        if r.getMessage() == "AI job cleanup failed: record interrupted attempt"
    )
    assert record.exc_info[0] is RuntimeError


@pytest.mark.asyncio
async def test_run_detached_gives_up_on_a_hung_write(caplog):
    with caplog.at_level(logging.WARNING):
        await run_detached(asyncio.Event().wait(), timeout=0.01, description="record")

    record = next(
        r for r in caplog.records if r.getMessage() == "AI job cleanup failed: record"
    )
    assert record.exc_info[0] is TimeoutError


@pytest.mark.asyncio
async def test_run_detached_write_outlives_a_cancelled_caller():
    gate = asyncio.Event()
    written = asyncio.Event()

    async def write():
        await gate.wait()
        written.set()

    before = set(supervision._DETACHED)
    caller = asyncio.ensure_future(
        run_detached(write(), timeout=2, description="record")
    )
    await asyncio.sleep(0)
    (task,) = supervision._DETACHED - before

    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not task.done()

    gate.set()
    await asyncio.wait_for(task, 2)
    await asyncio.sleep(0)

    assert written.is_set()
    assert task not in supervision._DETACHED

"""run_to_completion: work keeps going when the caller that awaits it is cancelled."""

from __future__ import annotations

import asyncio
import logging

import pytest

from src.app.services import cancellation
from src.app.services.cancellation import run_to_completion


async def _cancel_caller_mid_work(work, description: str) -> asyncio.Task:
    """Start ``work``, cancel the caller while it runs, return the work's task."""
    before = set(cancellation._RUNNING)
    caller = asyncio.create_task(run_to_completion(work, description=description))
    await asyncio.sleep(0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    [task] = cancellation._RUNNING - before
    return task


@pytest.mark.asyncio
async def test_returns_the_result():
    async def work() -> int:
        return 42

    assert await run_to_completion(work(), description="answer") == 42
    assert not cancellation._RUNNING


@pytest.mark.asyncio
async def test_a_waiting_caller_gets_the_failure_and_nothing_is_logged(caplog):
    async def work() -> None:
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger=cancellation.__name__):
        with pytest.raises(ValueError, match="boom"):
            await run_to_completion(work(), description="failing work")

    assert caplog.records == []
    assert not cancellation._RUNNING


@pytest.mark.asyncio
async def test_work_finishes_after_its_caller_is_cancelled():
    release = asyncio.Event()

    async def work() -> str:
        await release.wait()
        return "saved"

    task = await _cancel_caller_mid_work(work(), "save")

    # The caller saw the cancellation before the work was done.
    assert not task.done()
    release.set()
    assert await asyncio.wait_for(task, timeout=1) == "saved"
    assert task not in cancellation._RUNNING


@pytest.mark.asyncio
async def test_failure_after_the_caller_is_cancelled_is_logged(caplog):
    release = asyncio.Event()

    async def work() -> None:
        await release.wait()
        raise RuntimeError("database went away")

    task = await _cancel_caller_mid_work(work(), "saving meal m-1")

    with caplog.at_level(logging.ERROR, logger=cancellation.__name__):
        release.set()
        await asyncio.wait({task}, timeout=1)
        await asyncio.sleep(0)

    [record] = caplog.records
    assert (
        record.getMessage() == "saving meal m-1 failed after its caller was cancelled"
    )
    assert isinstance(record.exc_info[1], RuntimeError)
    assert task not in cancellation._RUNNING


@pytest.mark.asyncio
async def test_work_cancelled_on_its_own_is_not_logged(caplog):
    async def work() -> None:
        await asyncio.sleep(30)

    task = await _cancel_caller_mid_work(work(), "slow work")

    with caplog.at_level(logging.ERROR, logger=cancellation.__name__):
        task.cancel()
        await asyncio.wait({task}, timeout=1)
        await asyncio.sleep(0)

    assert task.cancelled()
    assert caplog.records == []
    assert task not in cancellation._RUNNING

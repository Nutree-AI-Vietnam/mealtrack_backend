"""AiJobJanitor deletes expired finished jobs in committed batches."""

from __future__ import annotations

import asyncio
import logging

import pytest
from tests.fixtures.fakes.fake_ai_jobs import FakeAiJobsUow

from src.app.services.ai_jobs.janitor import AiJobJanitor

LOGGER = "src.app.services.ai_jobs.janitor"


def _janitor(uow: FakeAiJobsUow, **overrides) -> AiJobJanitor:
    values = {
        "uow_factory": uow,
        "retention_days": 30,
        "interval_seconds": 0.01,
        "first_delay_seconds": 0.0,
        "batch_size": 100,
        "max_batches": 3,
    }
    return AiJobJanitor(**{**values, **overrides})


@pytest.mark.asyncio
async def test_a_pass_deletes_batch_after_batch_until_one_comes_back_short():
    uow = FakeAiJobsUow()
    uow.ai_jobs.delete_finished.side_effect = [100, 40]

    assert await _janitor(uow).run_once() == 140

    assert uow.ai_jobs.delete_finished.await_count == 2
    uow.ai_jobs.delete_finished.assert_awaited_with(older_than_days=30, limit=100)
    # Each batch is its own committed transaction.
    assert uow.committed == 2


@pytest.mark.asyncio
async def test_a_pass_stops_after_its_batch_limit():
    uow = FakeAiJobsUow()
    uow.ai_jobs.delete_finished.return_value = 100

    assert await _janitor(uow).run_once() == 300

    assert uow.ai_jobs.delete_finished.await_count == 3


@pytest.mark.asyncio
async def test_run_keeps_going_after_a_failed_pass_and_stops_on_request(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    uow = FakeAiJobsUow()
    passes: list[int] = []

    async def delete_finished(**_):
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("database went away")
        if len(passes) == 2:
            return 7
        stop.set()
        return 0

    uow.ai_jobs.delete_finished.side_effect = delete_finished
    stop = asyncio.Event()

    await asyncio.wait_for(_janitor(uow).run(stop), timeout=1)

    assert len(passes) == 3
    messages = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert messages == [
        "AI job cleanup pass failed",
        "Deleted 7 finished AI jobs older than 30 days",
    ]


@pytest.mark.asyncio
async def test_run_does_nothing_until_the_tables_are_migrated():
    uow = FakeAiJobsUow()
    checks: list[int] = []
    stop = asyncio.Event()

    async def ready() -> bool:
        checks.append(1)
        if len(checks) == 2:
            stop.set()
        return False

    await asyncio.wait_for(_janitor(uow, ready=ready).run(stop), timeout=1)

    uow.ai_jobs.delete_finished.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stop_during_the_first_delay_ends_run_without_a_pass():
    uow = FakeAiJobsUow()
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(_janitor(uow, first_delay_seconds=60.0).run(stop), timeout=1)

    uow.ai_jobs.delete_finished.assert_not_awaited()

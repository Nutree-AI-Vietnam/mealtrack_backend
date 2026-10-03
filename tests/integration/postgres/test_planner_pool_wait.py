"""Pool timing covers real asynchronous admission wait, without changing bounds."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infra.database.planner_pool import PlannerQueuePool
from src.planner_observability import planner_request_context

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_checkout_span_includes_bounded_queue_wait(
    migrated_database, monkeypatch
):
    durations = []

    def metric(_name, duration, *, unit, attributes):
        if attributes["phase"] == "checkout":
            durations.append(duration)

    monkeypatch.setattr("src.planner_observability.distribution_metric", metric)
    engine = create_async_engine(
        migrated_database,
        poolclass=PlannerQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    factory = async_sessionmaker(engine)
    try:
        async with factory() as holder:
            await holder.execute(text("SELECT 1"))
            entered = asyncio.Event()

            async def waiting_reader():
                with planner_request_context("current", "synthetic-test"):
                    async with factory() as session:
                        entered.set()
                        assert (
                            await session.execute(text("SELECT 1"))
                        ).scalar_one() == 1

            task = asyncio.create_task(waiting_reader())
            await entered.wait()
            await asyncio.sleep(0.05)
            await holder.rollback()
            await task
        assert len(durations) == 1
        assert durations[0] >= 25
        assert engine.sync_engine.pool.size() == 1
        assert engine.sync_engine.pool._max_overflow == 0
    finally:
        await engine.dispose()

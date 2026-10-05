"""Real worker transaction and blocked-snapshot lease boundaries on PG."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from tests.integration.postgres.catalog_preparation_fixtures import (
    claim,
    seed_preparation,
)

from src.domain.ports.catalog_preparation_port import (
    PreparationOutcome,
    PreparationResult,
)
from src.infra.database.models.meal_recommendation.catalog_preparation import (
    CatalogPreparationJobORM as Job,
)
from src.infra.repositories.catalog_preparation_repository import (
    AsyncCatalogPreparationRepository,
)
from src.infra.repositories.catalog_publication_fence import catalog_publication_version
from src.infra.workers.catalog_preparation_worker import CatalogPreparationWorker

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_worker_renews_short_remaining_snapshot_lease_before_provider(
    pg_session, async_session_factory, monkeypatch
):
    await seed_preparation(pg_session)
    original = AsyncCatalogPreparationRepository.load_input

    async def short_snapshot(repo, claimed):
        preparation = await original(repo, claimed)
        # Reproduce a snapshot that consumed almost all admission lease time,
        # without a long wall-clock delay in the test.
        await repo.session.execute(
            update(Job)
            .where(Job.id == claimed.id)
            .values(lease_expires_at=func.clock_timestamp() + timedelta(seconds=1))
        )
        return preparation

    monkeypatch.setattr(AsyncCatalogPreparationRepository, "load_input", short_snapshot)
    from tests.integration.postgres.catalog_preparation_fixtures import micros_result

    class Computer:
        async def compute(self, preparation):
            async with async_session_factory() as session:
                remaining = (
                    await session.execute(
                        select(Job.lease_expires_at - func.clock_timestamp()).where(
                            Job.id == preparation.claim.id
                        )
                    )
                ).scalar_one()
                assert remaining.total_seconds() > 100
            return micros_result()

    assert await CatalogPreparationWorker(async_session_factory, Computer()).run_once()


@pytest.mark.asyncio
async def test_worker_provider_computation_runs_with_no_database_checkout(
    pg_session, async_session_factory
):
    ids, _ = await seed_preparation(pg_session, task="translation")
    calls = []

    class Computer:
        async def compute(self, preparation):
            calls.append(preparation.claim.id)
            assert async_session_factory.kw["bind"].sync_engine.pool.checkedout() == 0
            assert preparation.references
            return PreparationResult(
                PreparationOutcome.READY,
                {"translations": {preparation.meal.name: "Prepared name"}},
            )

    worker = CatalogPreparationWorker(async_session_factory, Computer())
    assert await worker.run_once()
    assert len(calls) == 1
    async with async_session_factory() as session:
        overlay = await AsyncCatalogPreparationRepository(session).load_translations(
            ids[:1], locale="vi"
        )
        assert list(overlay[ids[0]].values()) == ["Prepared name"]


@pytest.mark.asyncio
async def test_prepared_ingredient_names_are_readable_by_food_reference(
    pg_session, async_session_factory
):
    ids, food_id = await seed_preparation(pg_session, task="translation")

    class Computer:
        async def compute(self, preparation):
            return PreparationResult(
                PreparationOutcome.READY,
                {"translations": {preparation.meal.name: "Tên", "Tofu": " Đậu phụ "}},
            )

    assert await CatalogPreparationWorker(async_session_factory, Computer()).run_once()
    async with async_session_factory() as session:
        repo = AsyncCatalogPreparationRepository(session)
        assert await repo.load_ingredient_translations(
            [food_id, None], locale="vi"
        ) == {food_id: "Đậu phụ"}
        assert await repo.load_ingredient_translations([food_id], locale="fr") == {}


@pytest.mark.asyncio
async def test_blocked_snapshot_reclaimed_lease_never_starts_old_provider(
    pg_session, async_session_factory
):
    await seed_preparation(pg_session)
    calls = []

    class Computer:
        async def compute(self, preparation):
            calls.append(preparation.claim.id)
            pytest.fail("An expired, reclaimed snapshot must never reach a provider")

    worker = CatalogPreparationWorker(async_session_factory, Computer())
    async with async_session_factory() as blocker:
        await catalog_publication_version(blocker, shared=False)
        task = asyncio.create_task(worker.run_once())
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.1)
            async with async_session_factory() as session:
                job_id = (
                    await session.execute(select(Job.id).where(Job.status == "running"))
                ).scalar_one()
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        lease_expires_at=func.clock_timestamp() - timedelta(seconds=1)
                    )
                )
                await session.commit()
            replacement = await claim(async_session_factory)
            assert replacement.id == job_id and replacement.attempts == 2
        finally:
            await blocker.rollback()
        assert await asyncio.wait_for(task, 5)
    assert calls == []


@pytest.mark.asyncio
async def test_projection_job_rebuilds_and_enqueues_exact_facets_without_provider(
    pg_session, async_session_factory
):
    ids, _ = await seed_preparation(pg_session, task="projection")
    from src.infra.database.models.meal_recommendation.catalog_recipe import (
        MealCatalogORM,
    )

    await pg_session.execute(
        update(MealCatalogORM)
        .where(MealCatalogORM.id == ids[0])
        .values(name="Published changed text")
    )
    await pg_session.commit()

    class Computer:
        async def compute(self, _):
            pytest.fail("Projection reconciliation must not call providers")

    assert await CatalogPreparationWorker(async_session_factory, Computer()).run_once()
    async with async_session_factory() as session:
        rows = (
            (await session.execute(select(Job).where(Job.catalog_meal_id == ids[0])))
            .scalars()
            .all()
        )
        assert {row.task for row in rows} == {
            "projection",
            "translation",
            "micronutrients",
        }
        assert (
            next(row for row in rows if row.task == "projection").status == "succeeded"
        )
        assert all(row.status == "pending" for row in rows if row.task != "projection")

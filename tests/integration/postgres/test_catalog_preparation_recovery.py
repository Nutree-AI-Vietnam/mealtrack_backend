"""Durable queue admission, finite retries and stale-worker recovery on PG."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from tests.integration.postgres.catalog_preparation_fixtures import (
    claim,
    job_state,
    micros_result,
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

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_duplicate_nontranslation_sentinel_jobs_are_idempotent(pg_session):
    ids, _ = await seed_preparation(pg_session, task=None)
    assert (
        await AsyncCatalogPreparationRepository(pg_session).enqueue_for_recipes(
            ids[:1], locales=("vi", "vi")
        )
        == 0
    )
    rows = (await pg_session.execute(select(Job))).scalars().all()
    assert sorted((row.task, row.locale) for row in rows) == [
        ("micronutrients", ""),
        ("translation", "vi"),
    ]


@pytest.mark.asyncio
async def test_expired_crashed_claim_is_reclaimed_and_old_token_cannot_publish(
    pg_session, async_session_factory
):
    await seed_preparation(pg_session)
    old = await claim(async_session_factory)
    async with async_session_factory() as session:
        await session.execute(
            update(Job)
            .where(Job.id == old.id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        await session.commit()
    replacement = await claim(async_session_factory)
    assert replacement.id == old.id
    assert replacement.claim_token != old.claim_token
    assert replacement.attempts == 2
    async with async_session_factory() as session:
        repo = AsyncCatalogPreparationRepository(session)
        assert not await repo.heartbeat(old)
        assert not await repo.complete(old, micros_result(99))
        await session.commit()
    async with async_session_factory() as session:
        repo = AsyncCatalogPreparationRepository(session)
        assert await repo.heartbeat(replacement)
        assert await repo.complete(replacement, micros_result(3))
        await session.commit()
    async with async_session_factory() as session:
        assert (
            await AsyncCatalogPreparationRepository(session).get_overlay(
                old.catalog_meal_id
            )
        )["micros"]["iron"] == 3


@pytest.mark.asyncio
async def test_retry_backoff_and_poison_job_exhaustion_are_finite(
    pg_session, async_session_factory
):
    await seed_preparation(pg_session)
    await pg_session.execute(update(Job).values(max_attempts=2))
    await pg_session.commit()
    first = await claim(async_session_factory)
    failure = PreparationResult(
        PreparationOutcome.RETRYABLE_FAILURE, error_code="provider_unavailable"
    )
    async with async_session_factory() as session:
        assert await AsyncCatalogPreparationRepository(session).complete(first, failure)
        await session.commit()
        row = await job_state(session, first.id)
        assert row.status == "retry_wait" and row.attempts == 1
        now = (await session.execute(select(func.now()))).scalar_one()
        assert 3 <= (row.available_at - now).total_seconds() <= 6
    assert await claim(async_session_factory) is None
    await pg_session.execute(
        update(Job)
        .where(Job.id == first.id)
        .values(available_at=func.now() - timedelta(seconds=1))
    )
    await pg_session.commit()
    second = await claim(async_session_factory)
    async with async_session_factory() as session:
        assert await AsyncCatalogPreparationRepository(session).complete(
            second, failure
        )
        await session.commit()
        assert (await job_state(session, first.id)).status == "failed"
    assert await claim(async_session_factory) is None


@pytest.mark.asyncio
async def test_multi_replica_claims_respect_global_provider_capacity(
    pg_session, async_session_factory
):
    await seed_preparation(pg_session, all_recipes=True)
    claims = await asyncio.wait_for(
        asyncio.gather(*(claim(async_session_factory, capacity=2) for _ in range(6))), 5
    )
    successful = [item for item in claims if item is not None]
    assert len(successful) == 2
    assert len({item.id for item in successful}) == 2
    async with async_session_factory() as session:
        assert (
            await session.execute(
                select(func.count()).select_from(Job).where(Job.status == "running")
            )
        ).scalar_one() == 2


@pytest.mark.asyncio
async def test_invalid_result_and_expired_exhausted_lease_are_terminal(
    pg_session, async_session_factory
):
    await seed_preparation(pg_session)
    claimed = await claim(async_session_factory)
    async with async_session_factory() as session:
        assert await AsyncCatalogPreparationRepository(session).complete(
            claimed,
            PreparationResult(
                PreparationOutcome.READY, {"micros": {"iron": float("nan")}}
            ),
        )
        await session.commit()
        assert (await job_state(session, claimed.id)).status == "failed"
    await pg_session.execute(
        update(Job)
        .where(Job.id == claimed.id)
        .values(
            status="running",
            attempts=5,
            max_attempts=5,
            claim_token="exhausted",
            lease_expires_at=func.now() - timedelta(seconds=1),
        )
    )
    await pg_session.commit()
    assert await claim(async_session_factory) is None
    assert (
        await job_state(pg_session, claimed.id)
    ).last_error_code == "retry_exhausted"

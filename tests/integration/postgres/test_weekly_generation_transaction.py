"""Final transaction races use real PostgreSQL ledger and owner/week locks."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from tests.integration.postgres.test_weekly_planner_optimization import WEEK, _seed

from src.app.commands.meal_planner import GenerateWeeklyMealPlanCommand
from src.app.services.weekly_meal_plan_service import WeeklyMealPlanService
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.infra.database.models.meal_write_operation import MealWriteOperationORM
from src.infra.database.models.weekly_meal_planner import WeeklyMealPlanORM
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)

pytestmark = pytest.mark.integration


def _command(user_id, key):
    return GenerateWeeklyMealPlanCommand(
        user_id=user_id,
        week_start_date=WEEK,
        timezone="UTC",
        preferences=WeeklyMealPlanPreferences(),
        idempotency_key=key,
        daily_calories=2000,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False])
async def test_missing_week_generate_race_completes_one_atomic_plan(
    pg_session,
    async_session_factory,
    monkeypatch,
    same_key,
):
    user_id, plan, _ = await _seed(pg_session)
    await pg_session.execute(
        delete(WeeklyMealPlanORM).where(WeeklyMealPlanORM.id == plan.id)
    )
    await pg_session.commit()
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    barrier = asyncio.Barrier(2)
    original = AsyncCatalogMealRepository.list_selection_candidates

    async def synchronized_candidates(self):
        meals = await original(self)
        await barrier.wait()
        return meals

    monkeypatch.setattr(
        AsyncCatalogMealRepository, "list_selection_candidates", synchronized_candidates
    )
    service = WeeklyMealPlanService(AsyncUnitOfWork)
    key = str(uuid4())
    results = await asyncio.gather(
        service.generate(_command(user_id, key)),
        service.generate(_command(user_id, key if same_key else str(uuid4()))),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, BaseException)]
    assert len(successes) == (2 if same_key else 1)
    if same_key:
        assert successes[0] == successes[1]
    else:
        assert any(
            isinstance(result, WeeklyMealPlanConflictError) for result in results
        )
    async with async_session_factory() as session:
        plans = (
            (
                await session.execute(
                    select(WeeklyMealPlanORM.id).where(
                        WeeklyMealPlanORM.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        operations = (
            (
                await session.execute(
                    select(MealWriteOperationORM).where(
                        MealWriteOperationORM.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert plans == [successes[0].id]
        assert len(operations) == 1 and operations[0].status == "completed"
        assert operations[0].target_meal_id == successes[0].id


@pytest.mark.asyncio
async def test_short_regeneration_records_current_algorithm_version(
    pg_session,
    async_session_factory,
    monkeypatch,
):
    user_id, plan, _ = await _seed(pg_session)
    await pg_session.execute(
        update(WeeklyMealPlanORM)
        .where(WeeklyMealPlanORM.id == plan.id)
        .values(algorithm_version="stale")
    )
    await pg_session.commit()
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    service = WeeklyMealPlanService(AsyncUnitOfWork)

    regenerated = await service.generate(_command(user_id, str(uuid4())))

    assert regenerated.id == plan.id
    async with async_session_factory() as session:
        stored = (
            await session.execute(
                select(WeeklyMealPlanORM.algorithm_version).where(
                    WeeklyMealPlanORM.id == plan.id
                )
            )
        ).scalar_one()
    assert stored == service.generator.algorithm_version


@pytest.mark.asyncio
async def test_cancelled_precomputation_leaves_no_operation_reservation(
    pg_session,
    async_session_factory,
    monkeypatch,
):
    user_id, _, _ = await _seed(pg_session)
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    entered = asyncio.Event()

    async def wait_for_cancel(self):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        AsyncCatalogMealRepository, "list_selection_candidates", wait_for_cancel
    )
    task = asyncio.create_task(
        WeeklyMealPlanService(AsyncUnitOfWork).generate(_command(user_id, str(uuid4())))
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with async_session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(MealWriteOperationORM)
                .where(MealWriteOperationORM.user_id == user_id)
            )
        ).scalar_one()
        assert count == 0

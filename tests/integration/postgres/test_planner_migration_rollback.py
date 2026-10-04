"""Expand/contract rehearsal preserves committed user-owned planner state."""

import asyncio
import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from tests.integration.postgres.test_weekly_planner_optimization import _seed

from src.infra.repositories.weekly_meal_plan_repository_async import (
    AsyncWeeklyMealPlanRepository,
)

pytestmark = pytest.mark.integration
TABLES = (
    "weekly_meal_plans",
    "weekly_meal_plan_slots",
    "weekly_meal_plan_pantry_items",
    "weekly_grocery_item_state",
    "weekly_grocery_day_lines",
    "meal",
    "meal_write_operation",
)


async def _snapshot(factory):
    async with factory() as session:
        return {
            table: (
                await session.execute(
                    text(
                        f"SELECT COALESCE(jsonb_agg(to_jsonb(row) ORDER BY to_jsonb(row)), '[]'::jsonb) FROM {table} row"
                    )
                )
            ).scalar_one()
            for table in TABLES
        }


@pytest.mark.asyncio
async def test_both_optimization_migrations_rollback_preserve_plan_pantry_and_slots(
    pg_session,
    async_session_factory,
    test_database_url,
):
    user_id, plan, food_id = await _seed(pg_session)
    repo = AsyncWeeklyMealPlanRepository(pg_session)
    await repo.update_pantry(
        user_id=user_id,
        plan_id=plan.id,
        updates=[
            {
                "ingredient_id": food_id,
                "available_amount": 50,
                "available_unit": "g",
                "checked": True,
                "do_not_buy": True,
                "manually_owned": True,
            }
        ],
    )
    await repo.replace_grocery_day_lines(
        user_id=user_id,
        plan_id=plan.id,
        ingredient_id=food_id,
        lines=[{"day_index": 0, "needed_amount": 25, "covered": True}],
    )
    await pg_session.commit()
    before = await _snapshot(async_session_factory)
    assert len(before["weekly_meal_plans"]) == 1
    assert len(before["weekly_meal_plan_slots"]) == 21
    assert before["weekly_meal_plan_pantry_items"]
    env = os.environ.copy()
    sync_url = test_database_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )
    env.update(
        DATABASE_URL=sync_url,
        DATABASE_URL_DIRECT=sync_url,
        MIGRATION_DATABASE_URL=sync_url,
    )

    def migrate(action):
        subprocess.run(
            [sys.executable, "migrations/cli.py", action],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    try:
        await asyncio.to_thread(migrate, "downgrade")
        await asyncio.to_thread(migrate, "downgrade")
        assert await _snapshot(async_session_factory) == before
    finally:
        await asyncio.to_thread(migrate, "upgrade")
    assert await _snapshot(async_session_factory) == before

from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.services.weekly_budget_target_sync import (
    sync_weekly_budget_targets_if_stale,
    weekly_targets_from_daily_macros,
)
from src.domain.model.weekly import WeeklyMacroBudget


def test_weekly_targets_derive_calories_from_macros():
    assert weekly_targets_from_daily_macros(
        {"protein": 150.0, "carbs": 200.0, "fat": 65.0}
    ) == {
        "target_calories": 13895.0,
        "target_protein": 1050.0,
        "target_carbs": 1400.0,
        "target_fat": 455.0,
    }


@pytest.mark.asyncio
async def test_sync_rewrites_stale_revision_and_targets():
    budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="u1",
        week_start_date=date(2026, 9, 7),
        target_calories=13366.5,
        target_protein=797.3,
        target_carbs=1594.6,
        target_fat=422.1,
        target_revision=1,
    )
    uow = AsyncMock()
    uow.weekly_budgets.update = AsyncMock(return_value=budget)

    updated = await sync_weekly_budget_targets_if_stale(
        uow,
        budget,
        daily_macros={"protein": 157.0, "carbs": 344.0, "fat": 70.0},
        profile_target_revision=7,
    )

    uow.weekly_budgets.update.assert_awaited_once_with(budget)
    assert updated.target_revision == 7
    assert updated.target_protein == 1099.0
    assert updated.target_carbs == 2408.0
    assert updated.target_fat == 490.0
    assert updated.target_calories == 18438.0


@pytest.mark.asyncio
async def test_sync_skips_write_when_revision_and_targets_match():
    budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="u1",
        week_start_date=date(2026, 9, 7),
        target_calories=13895.0,
        target_protein=1050.0,
        target_carbs=1400.0,
        target_fat=455.0,
        target_revision=3,
    )
    uow = AsyncMock()
    uow.weekly_budgets.update = AsyncMock()

    updated = await sync_weekly_budget_targets_if_stale(
        uow,
        budget,
        daily_macros={"protein": 150.0, "carbs": 200.0, "fat": 65.0},
        profile_target_revision=3,
    )

    uow.weekly_budgets.update.assert_not_awaited()
    assert updated.target_revision == 3

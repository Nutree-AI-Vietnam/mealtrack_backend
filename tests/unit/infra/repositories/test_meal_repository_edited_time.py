"""Saving an existing meal writes its edited log time and meal type."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.model.meal import MealStatus
from src.infra.repositories.meal_repository_async import AsyncMealRepository


@pytest.mark.asyncio
async def test_save_writes_the_edited_time_before_the_reload_reads_it():
    repo = AsyncMealRepository(session=MagicMock())
    lock_result = MagicMock()
    lock_result.scalar_one_or_none.return_value = "meal-1"
    row = MagicMock(
        nutrition=None,
        created_at=datetime(2026, 10, 28, 1, 15, tzinfo=UTC),
        meal_type="breakfast",
    )
    load_result = MagicMock()
    load_result.scalars.return_value.first.return_value = row
    repo.session.execute = AsyncMock(side_effect=[lock_result, load_result])
    flushed: list[tuple[datetime, str]] = []
    repo.session.flush = AsyncMock(
        side_effect=lambda: flushed.append((row.created_at, row.meal_type))
    )
    repo._capture_scan_baseline = AsyncMock()
    repo._reload_meal_domain = AsyncMock(return_value=MagicMock())
    edited_at = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
    meal = MagicMock(
        meal_id="meal-1",
        status=MealStatus.READY,
        created_at=edited_at,
        meal_type="lunch",
        image=None,
        nutrition=None,
        instructions=None,
        updated_at=None,
    )

    await repo.save(meal)

    assert flushed == [(edited_at, "lunch")]
    repo._reload_meal_domain.assert_awaited_once()

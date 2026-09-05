"""Tests for GetProgressSummaryQueryHandler."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from src.app.handlers.query_handlers.get_progress_summary_query_handler import (
    GetProgressSummaryQueryHandler,
)
from src.app.queries.progress.get_progress_summary_query import GetProgressSummaryQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import Nutrition
from src.domain.model.nutrition.macros import Macros
from src.domain.services.progress_summary_window import PROGRESS_SUMMARY_CAP_DAYS

USER_ID = "22222222-2222-2222-2222-222222222222"


def _meal(day: date, protein: float, carbs: float = 10.0, fat: float = 5.0) -> Meal:
    created = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
    return Meal(
        meal_id="11111111-1111-1111-1111-111111111111",
        user_id=USER_ID,
        status=MealStatus.READY,
        created_at=created,
        image=None,
        dish_name="Test",
        nutrition=Nutrition(macros=Macros(protein=protein, carbs=carbs, fat=fat)),
        ready_at=created,
    )


def _user(created_on: date):
    user = MagicMock()
    user.created_at = datetime(
        created_on.year, created_on.month, created_on.day, tzinfo=UTC
    )
    return user


class _Cheat:
    def __init__(self, d: date):
        self.date = d


def _uow(
    *,
    meals=None,
    hydration=None,
    movement=None,
    cheats=None,
    snapshots=None,
    weekly=None,
    created_on=date(2026, 1, 1),
    profile=None,
):
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.find_by_id = AsyncMock(return_value=_user(created_on))
    uow.users.get_profile = AsyncMock(return_value=profile)
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    uow.meals.find_by_date_range = AsyncMock(return_value=meals or [])
    uow.hydration_entries.sum_ml_by_date_range = AsyncMock(
        return_value=hydration or {}
    )
    uow.hydration_entries.sum_macros_by_date_range = AsyncMock(return_value={})
    uow.hydration_entries.sum_micros_by_date_range = AsyncMock(return_value={})
    uow.movement_entries.fetch_included_kcal_for_range = AsyncMock(
        return_value=movement or []
    )
    uow.cheat_days.find_by_user_and_date_range = AsyncMock(return_value=cheats or [])
    uow.daily_target_snapshots.find_calories_by_range = AsyncMock(
        return_value=snapshots or {}
    )
    uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=weekly)
    return uow


def _targets():
    # calories, protein, carbs, fat, revision, bmr
    return (2000.0, 140.0, 200.0, 70.0, 3, 1600.0)


async def _handle(uow, query, cache=None):
    handler = GetProgressSummaryQueryHandler(cache_service=cache)
    with (
        patch(
            "src.app.handlers.query_handlers.get_progress_summary_query_handler.AsyncUnitOfWork",
            return_value=uow,
        ),
        patch(
            "src.app.handlers.query_handlers.get_progress_summary_query_handler.resolve_user_timezone_async",
            new=AsyncMock(return_value="UTC"),
        ),
        patch(
            "src.app.handlers.query_handlers.get_progress_summary_query_handler.datetime"
        ) as mock_dt,
        patch(
            "src.app.handlers.query_handlers.get_progress_summary_query_handler.load_tdee_targets",
            new=AsyncMock(return_value=_targets()),
        ),
        patch(
            "src.app.handlers.query_handlers.get_progress_summary_query_handler.resolve_today_live_target",
            new=AsyncMock(return_value=1850.0),
        ),
    ):
        mock_dt.now.return_value.date.return_value = date(2026, 9, 1)
        mock_dt.now.return_value = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        return await handler.handle(query)


@pytest.mark.asyncio
async def test_seven_day_happy_path_and_derived_calories():
    start = date(2026, 8, 26)
    meals = [_meal(start, 10.0, 20.0, 5.0)]
    uow = _uow(meals=meals, cheats=[_Cheat(start)])
    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID, start_date=start, end_date=date(2026, 9, 1)
        ),
    )
    assert result["cap_days"] == PROGRESS_SUMMARY_CAP_DAYS
    assert result["effective_start"] == "2026-08-26"
    assert result["effective_end"] == "2026-09-01"
    assert len(result["days"]) == 7
    first = result["days"][0]
    assert first["calories"] == 165.0
    assert first["logged_status"] == "partial"
    assert first["is_cheat_day"] is True
    today = result["days"][-1]
    assert today["target_source"] == "adjusted_live"
    assert today["target_calories"] == 1850.0
    uow.meals.find_by_date_range.assert_awaited_once()
    uow.users.find_by_id.assert_awaited_once_with(UUID(USER_ID))


@pytest.mark.asyncio
async def test_snapshot_and_base_target_sources():
    yesterday = date(2026, 8, 31)
    older = date(2026, 8, 30)
    uow = _uow(snapshots={yesterday: 1777.0})
    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID, start_date=older, end_date=date(2026, 9, 1)
        ),
    )
    by_date = {row["date"]: row for row in result["days"]}
    assert by_date["2026-08-31"]["target_source"] == "snapshot"
    assert by_date["2026-08-31"]["target_calories"] == 1777.0
    assert by_date["2026-08-30"]["target_source"] == "base"
    assert by_date["2026-08-30"]["target_calories"] == 2000.0
    assert by_date["2026-09-01"]["target_source"] == "adjusted_live"


@pytest.mark.asyncio
async def test_clamps_pre_created_at_and_oversize_window():
    uow = _uow(created_on=date(2026, 8, 20))
    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID,
            start_date=date(2020, 1, 1),
            end_date=date(2026, 12, 1),
        ),
    )
    assert result["effective_start"] == "2026-08-20"
    assert result["effective_end"] == "2026-09-01"


@pytest.mark.asyncio
async def test_empty_range_rows_are_unlogged():
    uow = _uow()
    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        ),
    )
    assert result["days"][0]["logged_status"] == "none"
    assert result["days"][0]["calories"] == 0.0


@pytest.mark.asyncio
async def test_cache_hit_skips_compute():
    cache = MagicMock()
    cached = {
        "effective_start": "2026-09-01",
        "days": [],
        "target_revision": 3,
    }
    cache.get_json = AsyncMock(return_value=cached)
    cache.set_json = AsyncMock()
    uow = _uow()
    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        ),
        cache=cache,
    )
    assert result is cached
    uow.meals.find_by_date_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_another_user_id_is_passed_to_all_repos():
    other = "33333333-3333-3333-3333-333333333333"
    uow = _uow()
    await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=other,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        ),
    )
    uow.users.find_by_id.assert_awaited_once_with(UUID(other))
    assert uow.meals.find_by_date_range.await_args.args[0] == other
    assert uow.daily_target_snapshots.find_calories_by_range.await_args.args[0] == other

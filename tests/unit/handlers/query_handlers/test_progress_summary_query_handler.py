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
from src.domain.model.meal_projection import MealProjection
from src.domain.model.movement import MovementEntry
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
    uow.hydration_entries.sum_ml_by_date_range = AsyncMock(return_value=hydration or {})
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
async def test_auto_adjust_off_uses_base_for_every_day():
    yesterday = date(2026, 8, 31)
    older = date(2026, 8, 30)
    uow = _uow(snapshots={yesterday: 1777.0, older: 1650.0})
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=False)
    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID, start_date=older, end_date=date(2026, 9, 1)
        ),
    )
    by_date = {row["date"]: row for row in result["days"]}
    assert result["weekly_auto_adjust"] is False
    assert by_date["2026-08-30"]["target_source"] == "base"
    assert by_date["2026-08-30"]["target_calories"] == 2000.0
    assert by_date["2026-08-31"]["target_source"] == "base"
    assert by_date["2026-08-31"]["target_calories"] == 2000.0
    assert by_date["2026-09-01"]["target_source"] == "base"
    assert by_date["2026-09-01"]["target_calories"] == 2000.0


@pytest.mark.asyncio
async def test_cache_miss_when_auto_adjust_differs():
    cache = MagicMock()
    cache.get_json_with_revision = AsyncMock(
        return_value=(
            {
                "effective_start": "2026-09-01",
                "days": [{"date": "2026-09-01", "target_calories": 1850.0}],
                "target_revision": 3,
                "intake_revision": 4,
                "weekly_auto_adjust": True,
            },
            4,
        )
    )
    cache.set_json = AsyncMock()
    uow = _uow()
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=False)
    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        ),
        cache=cache,
    )
    assert result["weekly_auto_adjust"] is False
    assert result["days"][0]["target_calories"] == 2000.0
    uow.meals.find_by_date_range.assert_awaited()


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
        "intake_revision": 4,
    }
    cache.get_json_with_revision = AsyncMock(return_value=(cached, 4))
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
    assert result["effective_start"] == "2026-09-01"
    assert result["target_revision"] == 3
    uow.meals.find_by_date_range.assert_not_awaited()
    uow.hydration_entries.sum_ml_by_date_range.assert_awaited()


@pytest.mark.asyncio
async def test_summary_cached_before_a_meal_write_is_recomputed():
    cache = MagicMock()
    cache.get_json_with_revision = AsyncMock(
        return_value=(
            {
                "effective_start": "2026-09-01",
                "days": [],
                "target_revision": 3,
                "intake_revision": 4,
            },
            5,
        )
    )
    cache.set_json = AsyncMock()
    uow = _uow()

    await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        ),
        cache=cache,
    )

    uow.meals.find_by_date_range.assert_awaited()
    assert cache.set_json.await_args.args[1]["intake_revision"] == 5


@pytest.mark.asyncio
async def test_tdee_checkout_does_not_overlap_the_summary_session():
    """TDEE opens its own session. The summary session must already be closed."""
    uow = _uow()
    open_sessions = 0
    open_during_tdee: int | None = None

    async def enter():
        nonlocal open_sessions
        open_sessions += 1
        return uow

    async def exit_uow(*_args):
        nonlocal open_sessions
        open_sessions -= 1
        return False

    uow.__aenter__ = AsyncMock(side_effect=enter)
    uow.__aexit__ = AsyncMock(side_effect=exit_uow)

    async def load_targets(_user_id, _cache):
        nonlocal open_during_tdee
        open_during_tdee = open_sessions
        return _targets()

    handler = GetProgressSummaryQueryHandler()
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
            new=AsyncMock(side_effect=load_targets),
        ),
        patch(
            "src.app.handlers.query_handlers.get_progress_summary_query_handler.resolve_today_live_target",
            new=AsyncMock(return_value=1850.0),
        ),
    ):
        mock_dt.now.return_value = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        await handler.handle(
            GetProgressSummaryQuery(
                user_id=USER_ID,
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 1),
            )
        )

    assert open_during_tdee == 0
    assert uow.__aenter__.await_count >= 2
    assert open_sessions == 0


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


def _run(day: date, minutes: int, included: bool):
    return MovementEntry(
        user_id=USER_ID,
        activity_name="Running",
        duration_min=minutes,
        kcal_burned=200.0,
        intensity="moderate",
        logged_at=datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC),
        activity_id="running",
        include_in_balance=included,
    )


@pytest.mark.asyncio
async def test_trends_loads_food_items_and_movement_once_and_adds_goals():
    start, end = date(2026, 8, 26), date(2026, 9, 1)
    uow = _uow(meals=[_meal(start, 10.0)])
    uow.movement_entries.find_by_user_and_logged_range = AsyncMock(
        return_value=[_run(start, 30, True), _run(start, 20, False)]
    )
    cache = MagicMock()
    cache.get_json_with_revision = AsyncMock(return_value=(None, 7))
    cache.set_json = AsyncMock()

    result = await _handle(
        uow,
        GetProgressSummaryQuery(
            user_id=USER_ID, start_date=start, end_date=end, include_trends=True
        ),
        cache=cache,
    )

    kwargs = uow.meals.find_by_date_range.await_args.kwargs
    assert kwargs["projection"] is MealProjection.MACROS_WITH_MICROS
    uow.movement_entries.find_by_user_and_logged_range.assert_awaited_once()
    uow.movement_entries.fetch_included_kcal_for_range.assert_not_awaited()
    first = result["days"][0]
    assert first["burned_calories"] == 200.0
    assert first["movement_sessions"] == 2
    assert first["movement_load"] == 100.0
    assert result["days"][1]["movement_sessions"] == 0
    assert result["goals_revision"] == 3
    assert result["micro_goals"]["iron"] == 17.4
    assert result["micro_goals"]["saturated_fat"] == 22.2
    assert result["micro_upper_limits"]["iron"]["value"] == 45.0
    key = cache.set_json.await_args.args[0]
    assert key.endswith(":trends")


@pytest.mark.asyncio
async def test_default_summary_keeps_its_query_shape_and_cache_key():
    start, end = date(2026, 8, 26), date(2026, 9, 1)
    uow = _uow(
        meals=[_meal(start, 10.0)],
        movement=[(datetime(2026, 8, 26, 6, tzinfo=UTC), 120.0)],
    )
    uow.movement_entries.find_by_user_and_logged_range = AsyncMock(return_value=[])
    cache = MagicMock()
    cache.get_json_with_revision = AsyncMock(return_value=(None, 7))
    cache.set_json = AsyncMock()

    result = await _handle(
        uow,
        GetProgressSummaryQuery(user_id=USER_ID, start_date=start, end_date=end),
        cache=cache,
    )

    kwargs = uow.meals.find_by_date_range.await_args.kwargs
    assert kwargs["projection"] is MealProjection.MACROS_ONLY
    uow.movement_entries.find_by_user_and_logged_range.assert_not_awaited()
    assert result["days"][0]["burned_calories"] == 120.0
    assert "micro_goals" not in result
    assert "goals_revision" not in result
    assert "micros" not in result["days"][0]
    assert "movement_load" not in result["days"][0]
    key = cache.set_json.await_args.args[0]
    assert key == f"user:{USER_ID}:progress_summary:2026-08-26:2026-09-01"

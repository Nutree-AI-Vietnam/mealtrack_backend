from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.services.chat_context_builder import (
    _RECENT_MEAL_FETCH_LIMIT,
    ChatContextBuilder,
)
from src.domain.model.chat import CHAT_RECENT_MEAL_LIMIT
from src.domain.model.meal import MealStatus


class _Uow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_build_recovers_weekly_context_when_daily_cache_has_none() -> None:
    builder = ChatContextBuilder(uow_factory=lambda: _Uow())
    builder._recent_meals = AsyncMock(return_value=[])
    builder._safe_profile = AsyncMock(return_value={"profile": {}})
    builder._safe_tdee = AsyncMock(return_value={"tdee": 2200})
    builder._safe_daily = AsyncMock(
        return_value={
            "target_calories": 1800,
            "target_macros": {"protein": 140, "carbs": 180, "fat": 60},
            "total_calories": 900,
            "food_calories": 1150,
            "movement_kcal_burned": 250,
            "total_protein": 90,
            "total_carbs": 100,
            "total_fat": 40,
        }
    )
    builder._weekly_handler.handle = AsyncMock(
        return_value={
            "adjusted_daily_calories": 2000,
            "adjusted_daily_carbs": 220,
            "adjusted_daily_fat": 70,
            "daily_protein": 140,
            "remaining_days": 6,
        }
    )

    with patch(
        "src.app.services.chat_context_builder.resolve_user_timezone_async",
        new=AsyncMock(return_value="UTC"),
    ):
        context = await builder.build(
            user_id="u1",
            locale="en",
            header_timezone="UTC",
        )

    builder._weekly_handler.handle.assert_awaited_once()
    weekly_query = builder._weekly_handler.handle.await_args.args[0]
    assert weekly_query.user_id == "u1"
    assert weekly_query.target_date is not None
    assert weekly_query.header_timezone == "UTC"
    assert weekly_query.read_only is True
    assert context.target_calories == 2000
    assert context.target_carbs_g == 220
    assert context.remaining_days == 6
    assert context.food_calories == 1150
    assert context.movement_kcal_burned == 250
    assert context.remaining_calories == 850


@pytest.mark.asyncio
async def test_build_uses_today_remaining_not_weekly_leftover() -> None:
    builder = ChatContextBuilder(uow_factory=lambda: _Uow())
    builder._recent_meals = AsyncMock(return_value=[])
    builder._safe_profile = AsyncMock(return_value={"profile": {}})
    builder._safe_tdee = AsyncMock(return_value={"tdee": 2200})
    builder._safe_daily = AsyncMock(
        return_value={
            "target_calories": 1800,
            "target_macros": {"protein": 140, "carbs": 180, "fat": 60},
            "total_calories": 111,
            "food_calories": 111,
            "movement_kcal_burned": 0,
            "total_protein": 1,
            "total_carbs": 27,
            "total_fat": 0,
        }
    )
    builder._weekly_handler.handle = AsyncMock(
        return_value={
            "adjusted_daily_calories": 1932,
            "adjusted_daily_carbs": 222,
            "adjusted_daily_fat": 63,
            "daily_protein": 119,
            "remaining_days": 6,
            "remaining_calories": 12000,
        }
    )

    with patch(
        "src.app.services.chat_context_builder.resolve_user_timezone_async",
        new=AsyncMock(return_value="UTC"),
    ):
        context = await builder.build(
            user_id="u1",
            locale="en",
            header_timezone="UTC",
        )

    assert context.target_calories == 1932
    assert context.food_calories == 111
    assert context.remaining_calories == 1821


@pytest.mark.asyncio
async def test_build_gathers_context_concurrently() -> None:
    builder = ChatContextBuilder(uow_factory=lambda: _Uow())
    builder._recent_meals = AsyncMock(return_value=[])

    execution_order = []

    async def mock_profile(*args, **kwargs):
        execution_order.append("profile_start")
        return {"profile": {}}

    async def mock_tdee(*args, **kwargs):
        execution_order.append("tdee_start")
        return {"tdee": 2000}

    async def mock_daily(*args, **kwargs):
        execution_order.append("daily_start")
        return {}

    async def mock_weekly(*args, **kwargs):
        execution_order.append("weekly_start")
        return {}

    builder._safe_profile = mock_profile
    builder._safe_tdee = mock_tdee
    builder._safe_daily = mock_daily
    builder._safe_weekly_budget = mock_weekly

    with patch(
        "src.app.services.chat_context_builder.resolve_user_timezone_async",
        new=AsyncMock(return_value="UTC"),
    ):
        await builder.build(
            user_id="u1",
            locale="en",
            header_timezone="UTC",
        )

    assert set(execution_order) == {
        "profile_start",
        "tdee_start",
        "daily_start",
        "weekly_start",
    }


def test_coach_preferred_name_uses_display_name_then_first_name() -> None:
    from src.app.services.chat_context_builder import coach_preferred_name

    assert coach_preferred_name("  Alex  ", "Alexander") == "Alex"
    assert coach_preferred_name("", "Alexander") == "Alexander"
    assert coach_preferred_name(None, None) is None


@pytest.mark.asyncio
async def test_build_includes_preferred_name() -> None:
    class _User:
        display_name = "Alex"
        first_name = "Alexander"

    class _Users:
        async def find_by_id(self, user_id):
            return _User()

    class _NamedUow(_Uow):
        users = _Users()

    builder = ChatContextBuilder(uow_factory=lambda: _NamedUow())
    builder._recent_meals = AsyncMock(return_value=[])
    builder._safe_profile = AsyncMock(return_value={"profile": {}})
    builder._safe_tdee = AsyncMock(return_value={"tdee": 2200})
    builder._safe_daily = AsyncMock(
        return_value={
            "target_calories": 1800,
            "target_macros": {"protein": 140, "carbs": 180, "fat": 60},
            "total_calories": 900,
            "food_calories": 900,
            "movement_kcal_burned": 0,
            "total_protein": 90,
            "total_carbs": 100,
            "total_fat": 40,
        }
    )
    builder._weekly_handler.handle = AsyncMock(
        return_value={
            "adjusted_daily_calories": 1800,
            "adjusted_daily_carbs": 180,
            "adjusted_daily_fat": 60,
            "daily_protein": 140,
            "remaining_days": 4,
        }
    )

    with patch(
        "src.app.services.chat_context_builder.resolve_user_timezone_async",
        new=AsyncMock(return_value="UTC"),
    ):
        context = await builder.build(
            user_id="11111111-1111-1111-1111-111111111111",
            locale="en",
            header_timezone="UTC",
        )

    assert context.preferred_name == "Alex"
    assert context.to_prompt_dict()["preferred_name"] == "Alex"


class _Meals:
    def __init__(self, meals: list[object]) -> None:
        self.meals = meals
        self.limit: int | None = None

    async def find_by_date_range(self, *args, **kwargs):
        del args
        self.limit = kwargs["limit"]
        return self.meals[: self.limit]


class _UowWithMeals:
    def __init__(self, meals: _Meals) -> None:
        self.meals = meals

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_recent_meals_keeps_the_newest_dishes() -> None:
    meals = [
        SimpleNamespace(
            status=MealStatus.READY,
            created_at=datetime(2026, 10, 13, 8, index, tzinfo=UTC),
            nutrition=None,
            dish_name=f"Meal {index}",
            meal_id=str(index),
            meal_type="lunch",
        )
        for index in range(CHAT_RECENT_MEAL_LIMIT + 6)
    ]
    repo = _Meals(meals)
    builder = ChatContextBuilder(uow_factory=lambda: _UowWithMeals(repo))

    summaries = await builder._recent_meals(
        _UowWithMeals(repo),
        "u1",
        date(2026, 10, 13),
        "UTC",
    )

    assert repo.limit == _RECENT_MEAL_FETCH_LIMIT
    assert len(summaries) == CHAT_RECENT_MEAL_LIMIT
    assert summaries[-1].dish_name == f"Meal {CHAT_RECENT_MEAL_LIMIT + 5}"
    assert summaries[0].dish_name == "Meal 6"

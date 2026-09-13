from unittest.mock import AsyncMock, patch

import pytest

from src.app.services.chat_context_builder import ChatContextBuilder


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

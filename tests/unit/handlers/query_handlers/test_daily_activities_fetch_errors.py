"""A failed or partial daily activities fetch is an error, never a cached 200.

The app treats a successful daily feed as the whole day and drops local rows
the reply leaves out, so an empty or meals-only list after a DB error would
read as deletes.
"""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1.activities import router
from src.app.handlers.query_handlers.get_daily_activities_query_handler import (
    GetDailyActivitiesQueryHandler,
)
from src.app.queries.activity import GetDailyActivitiesQuery
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model import Macros, Meal, MealImage, MealStatus, Nutrition

HANDLER_MODULE = "src.app.handlers.query_handlers.get_daily_activities_query_handler"
USER_ID = "123e4567-e89b-12d3-a456-426614174100"
MEAL_ID = "123e4567-e89b-12d3-a456-426614174001"
DAY = datetime(2026, 10, 21)
DAILY_URL = "/v1/activities/daily?date=2026-10-21"


def _lunch() -> Meal:
    return Meal(
        meal_id=MEAL_ID,
        user_id=USER_ID,
        status=MealStatus.READY,
        created_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        image=MealImage(
            image_id="123e4567-e89b-12d3-a456-426614174010",
            format="jpeg",
            size_bytes=1,
            url=None,
        ),
        dish_name="Lunch",
        nutrition=Nutrition(
            macros=Macros(protein=30.0, carbs=50.0, fat=20.0), food_items=[]
        ),
        ready_at=datetime(2026, 10, 21, 12, 5, tzinfo=UTC),
    )


def _badminton():
    return SimpleNamespace(
        id="mov_1",
        logged_at=datetime(2026, 10, 21, 7, 0, tzinfo=UTC),
        activity_id="badminton",
        activity_name="Badminton",
        intensity="moderate",
        duration_min=30,
        kcal_burned=210.0,
        source="manual",
        include_in_balance=True,
    )


def _uow(*, meals=(), movements=(), meals_error=None, movements_error=None):
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals.find_by_date = AsyncMock(
        return_value=list(meals), side_effect=meals_error
    )
    uow.hydration_entries.find_by_date = AsyncMock(return_value=[])
    uow.movement_entries.find_by_user_and_logged_range = AsyncMock(
        return_value=list(movements), side_effect=movements_error
    )
    return uow


def _cache():
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=None)
    cache.set_json = AsyncMock()
    return cache


def _client(handler: GetDailyActivitiesQueryHandler) -> TestClient:
    class _Bus:
        async def send(self, query):
            return await handler.handle(query)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_configured_event_bus] = lambda: _Bus()
    # The catch-all handler answers 500, then re-raises for server logging.
    return TestClient(app, raise_server_exceptions=False)


def test_meal_query_error_returns_500_and_is_not_cached():
    cache = _cache()
    uow = _uow(meals_error=PoolTimeoutError("QueuePool limit reached"))

    with patch(f"{HANDLER_MODULE}.AsyncUnitOfWork", return_value=uow):
        response = _client(GetDailyActivitiesQueryHandler(cache_service=cache)).get(
            DAILY_URL
        )

    assert response.status_code == 500
    assert response.json()["detail"]["error_code"] == "INTERNAL_ERROR"
    cache.set_json.assert_not_awaited()


def test_movement_query_error_returns_500_instead_of_meals_only():
    cache = _cache()
    uow = _uow(
        meals=[_lunch()],
        movements_error=OperationalError("SELECT", {}, Exception("connection reset")),
    )

    with patch(f"{HANDLER_MODULE}.AsyncUnitOfWork", return_value=uow):
        response = _client(GetDailyActivitiesQueryHandler(cache_service=cache)).get(
            DAILY_URL
        )

    uow.meals.find_by_date.assert_awaited_once()
    assert response.status_code == 500
    assert response.json()["detail"]["error_code"] == "INTERNAL_ERROR"
    cache.set_json.assert_not_awaited()


def test_success_returns_whole_day_and_caches_it():
    cache = _cache()
    uow = _uow(meals=[_lunch()], movements=[_badminton()])

    with patch(f"{HANDLER_MODULE}.AsyncUnitOfWork", return_value=uow):
        response = _client(GetDailyActivitiesQueryHandler(cache_service=cache)).get(
            DAILY_URL
        )

    assert response.status_code == 200
    body = response.json()
    assert [(item["type"], item["id"]) for item in body] == [
        ("meal", MEAL_ID),
        ("movement", "mov_1"),
    ]
    cache.set_json.assert_awaited_once()
    key, cached, _ttl = cache.set_json.await_args.args
    assert key == CacheKeys.daily_activities(USER_ID, DAY.date())[0]
    assert cached == body


@pytest.mark.asyncio
async def test_handler_reraises_fetch_error_and_logs_warning(caplog):
    cache = _cache()
    uow = _uow(meals_error=PoolTimeoutError("QueuePool limit reached"))
    handler = GetDailyActivitiesQueryHandler(cache_service=cache)

    with (
        patch(f"{HANDLER_MODULE}.AsyncUnitOfWork", return_value=uow),
        caplog.at_level(logging.WARNING, logger=HANDLER_MODULE),
        pytest.raises(PoolTimeoutError),
    ):
        await handler.handle(GetDailyActivitiesQuery(user_id=USER_ID, target_date=DAY))

    records = [r for r in caplog.records if r.name == HANDLER_MODULE]
    assert [r.levelno for r in records] == [logging.WARNING]
    assert "fetch failed" in records[0].getMessage()
    cache.set_json.assert_not_awaited()

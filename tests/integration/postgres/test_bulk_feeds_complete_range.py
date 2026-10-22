"""Bulk feeds return every day of the range, however many meal rows it holds.

Every plain drink also writes a ``meal`` row, so a heavy logger reaches
hundreds of meal rows in the app's bulk window. The range read is ordered
oldest first, so any row cap drops the newest days, today included.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1 import activities, nutrition
from src.app.handlers.query_handlers.get_bulk_activities_query_handler import (
    GetBulkActivitiesQueryHandler,
)
from src.app.handlers.query_handlers.get_nutrition_bulk_query_handler import (
    GetNutritionBulkQueryHandler,
)
from src.app.queries.activity import GetBulkActivitiesQuery
from src.app.queries.nutrition import GetNutritionBulkQuery
from src.domain.model.hydration import HydrationEntry
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import Nutrition
from src.domain.utils.timezone_utils import get_zone_info
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.event_bus import PyMediatorEventBus

pytestmark = pytest.mark.integration

TZ = "Asia/Ho_Chi_Minh"
DAYS = 28
FOOD_PER_DAY = 4
DRINKS_PER_DAY = 20
ROWS_PER_DAY = FOOD_PER_DAY + DRINKS_PER_DAY
# protein 20 g, carbs 30 g, fat 10 g -> 20*4 + 30*4 + 10*9 = 290 kcal
FOOD_KCAL = 290.0


def _image() -> MealImage:
    return MealImage(image_id=str(uuid4()), format="jpeg", size_bytes=1, url=None)


def _food(user_id: str, at: datetime) -> Meal:
    return Meal(
        meal_id=str(uuid4()),
        user_id=user_id,
        status=MealStatus.READY,
        created_at=at,
        ready_at=at,
        image=_image(),
        dish_name="Com tam",
        meal_type="lunch",
        source="manual",
        nutrition=Nutrition(
            macros=Macros(protein=20.0, carbs=30.0, fat=10.0, fiber=0.0, sugar=0.0),
            food_items=None,
        ),
    )


def _plain_drink(user_id: str, at: datetime) -> Meal:
    """The meal row ``LogHydrationCommandHandler`` writes for plain water."""
    return Meal(
        meal_id=str(uuid4()),
        user_id=user_id,
        status=MealStatus.READY,
        created_at=at,
        ready_at=at,
        image=_image(),
        dish_name="Water",
        meal_type="hydration",
        source="hydration",
        quantity=250,
        nutrition=Nutrition(
            macros=Macros(protein=0.0, carbs=0.0, fat=0.0, fiber=0.0, sugar=0.0),
            food_items=None,
        ),
    )


async def _seed(user_id: str, start: date) -> None:
    zone = get_zone_info(TZ)
    async with AsyncUnitOfWork() as uow:
        uow.session.add(
            User(
                id=user_id,
                firebase_uid=user_id,
                email=f"{user_id}@example.test",
                username=user_id,
                password_hash="test",
            )
        )
        await uow.session.flush()
        for offset in range(DAYS):
            day = start + timedelta(days=offset)
            first = datetime.combine(day, time(7, 0), tzinfo=zone)
            for slot in range(ROWS_PER_DAY):
                at = first + timedelta(minutes=30 * slot)
                if slot % (ROWS_PER_DAY // FOOD_PER_DAY) == 0:
                    await uow.meals.save(_food(user_id, at))
                    continue
                saved = await uow.meals.save(_plain_drink(user_id, at))
                await uow.hydration_entries.add(
                    HydrationEntry(
                        user_id=user_id,
                        drink_id="water",
                        drink_name_snapshot="Water",
                        volume_ml=250,
                        credited_ml=250,
                        logged_at=at,
                        legacy_meal_id=saved.meal_id,
                    )
                )


def _client(user_id: str) -> httpx.AsyncClient:
    bus = PyMediatorEventBus()
    bus.register_handler(GetNutritionBulkQuery, GetNutritionBulkQueryHandler())
    bus.register_handler(GetBulkActivitiesQuery, GetBulkActivitiesQueryHandler())
    app = FastAPI()
    app.include_router(nutrition.router)
    app.include_router(activities.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Timezone": TZ},
    )


@pytest_asyncio.fixture
async def seeded(async_session_factory, pg_session, monkeypatch):
    """A user with 24 meal rows a day for 28 days ending today (672 rows)."""
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    user_id = str(uuid4())
    end = datetime.now(get_zone_info(TZ)).date()
    start = end - timedelta(days=DAYS - 1)
    await _seed(user_id, start)
    return user_id, start, end


@pytest.mark.asyncio
async def test_nutrition_bulk_covers_every_day_past_500_meal_rows(seeded):
    user_id, start, end = seeded
    async with _client(user_id) as client:
        response = await client.get(
            "/v1/nutrition/bulk", params={"start": start, "end": end}
        )

    assert response.status_code == 200
    dates = response.json()["dates"]
    days = [(start + timedelta(days=i)).isoformat() for i in range(DAYS)]
    assert [dates[day]["meal_count"] for day in days] == [ROWS_PER_DAY] * DAYS
    assert [dates[day]["totals"]["consumed"]["calories"] for day in days] == [
        FOOD_PER_DAY * FOOD_KCAL
    ] * DAYS
    assert dates[end.isoformat()]["has_meals"] is True


@pytest.mark.asyncio
async def test_range_read_returns_every_row_unless_a_limit_is_passed(seeded):
    user_id, start, end = seeded
    async with AsyncUnitOfWork(read_only=True) as uow:
        every = await uow.meals.find_by_date_range(
            user_id, start, end, user_timezone=TZ
        )
        capped = await uow.meals.find_by_date_range(
            user_id, start, end, limit=10, user_timezone=TZ
        )

    assert len(every) == DAYS * ROWS_PER_DAY
    assert [m.meal_id for m in capped] == [m.meal_id for m in every[:10]]


@pytest.mark.asyncio
async def test_bulk_activities_covers_every_day_past_500_meal_rows(seeded):
    user_id, start, end = seeded
    async with _client(user_id) as client:
        response = await client.get(
            "/v1/activities/bulk", params={"start": start, "end": end}
        )

    assert response.status_code == 200
    by_day = response.json()
    days = [(start + timedelta(days=i)).isoformat() for i in range(DAYS)]
    assert [len(by_day.get(day, [])) for day in days] == [ROWS_PER_DAY] * DAYS
    today = by_day[end.isoformat()]
    assert sum(1 for a in today if a["type"] == "meal") == FOOD_PER_DAY
    assert sum(1 for a in today if a["type"] == "hydration") == DRINKS_PER_DAY

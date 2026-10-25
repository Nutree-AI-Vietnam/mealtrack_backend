"""After a meal edit, the next read shows the edit, with no Queue worker running.

Covers the two reported bugs end to end through the real routes and Postgres:
a portion change that left Home's day total and today's list on the old kcal,
and a rename that came back as the old translated name. The cache behaves like
hosted Redis and the write-path purge is registered the way API startup does,
so nothing but the write itself can clear what it made stale.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import src.api.base_dependencies as base_dependencies
from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_cache_service,
    get_image_store,
    get_meal_translation_service,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.accept_language import AcceptLanguageMiddleware
from src.api.routes.v1 import activities, nutrition
from src.api.routes.v1.meals import router as meals_router
from src.app.commands.meal import EditMealCommand
from src.app.commands.user.save_user_onboarding_command import (
    SaveUserOnboardingCommand,
)
from src.app.events.hydration.local_cache_invalidation import (
    register_local_hydration_cache_invalidation_hook,
)
from src.app.events.meal.meal_events import register_local_cache_invalidation_hook
from src.app.events.movement.local_cache_invalidation import (
    register_local_movement_cache_invalidation_hook,
)
from src.app.handlers.command_handlers import (
    EditMealCommandHandler,
    SaveUserOnboardingCommandHandler,
)
from src.app.handlers.query_handlers import (
    GetDailyMacrosQueryHandler,
    GetMealByIdQueryHandler,
)
from src.app.handlers.query_handlers.get_daily_activities_query_handler import (
    GetDailyActivitiesQueryHandler,
)
from src.app.handlers.query_handlers.get_nutrition_bulk_query_handler import (
    GetNutritionBulkQueryHandler,
)
from src.app.queries.activity import GetDailyActivitiesQuery
from src.app.queries.meal.get_daily_macros_query import GetDailyMacrosQuery
from src.app.queries.meal.get_meal_by_id_query import GetMealByIdQuery
from src.app.queries.nutrition import GetNutritionBulkQuery
from src.domain.model.meal import (
    FoodItemTranslation,
    Meal,
    MealStatus,
    MealTranslation,
)
from src.domain.model.nutrition import FoodItem, Macros, Nutrition
from src.domain.utils.timezone_utils import get_zone_info
from src.infra.cache.cache_service import CacheService
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.event_bus import PyMediatorEventBus

pytestmark = pytest.mark.integration

TZ = "Asia/Ho_Chi_Minh"
VI = {"Accept-Language": "vi", "X-Timezone": TZ}
EN = {"Accept-Language": "en", "X-Timezone": TZ}
V2 = {
    "X-Nutrition-Contract-Version": "2",
    "X-App-Version": "2.8.3",
    "X-Platform": "ios",
}


class _MemoryRedis:
    """The RedisClient surface CacheService uses, kept in a dict."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ttl=None):
        self.values[key] = value
        return True

    async def set_if_revision_newer(self, key, value, revision, ttl):
        current = self.values.get(f"{key}:__revision")
        if current is not None and int(current) > revision:
            return False
        self.values[f"{key}:__revision"] = str(revision)
        self.values[key] = value
        return True

    async def delete(self, key):
        return self.values.pop(key, None) is not None

    async def delete_many(self, keys):
        return sum(self.values.pop(key, None) is not None for key in keys)

    async def delete_pattern(self, pattern):
        raise AssertionError(f"write path must not SCAN: {pattern}")

    async def get_counter(self, key):
        return int(self.values.get(key, 0))

    async def incr_with_expiry(self, key, ttl):
        self.values[key] = str(int(self.values.get(key, 0)) + 1)
        return int(self.values[key])


def _item(name: str, grams: float, protein: float, carbs: float, fat: float):
    return FoodItem(
        id=str(uuid4()),
        name=name,
        quantity=grams,
        unit="g",
        macros=Macros(protein=protein, carbs=carbs, fat=fat),
    )


def _meal(user_id: str, at: datetime, dish_name: str, item: FoodItem, raw=None):
    return Meal(
        meal_id=str(uuid4()),
        user_id=user_id,
        status=MealStatus.READY,
        created_at=at,
        ready_at=at,
        image=None,
        dish_name=dish_name,
        source="prompt",
        meal_type="breakfast",
        raw_gpt_json=json.dumps(raw, ensure_ascii=False) if raw else None,
        nutrition=Nutrition(macros=item.macros, food_items=[item]),
    )


def _vi_row(meal: Meal, dish_name: str, ingredient: str) -> MealTranslation:
    return MealTranslation(
        meal_id=meal.meal_id,
        language="vi",
        dish_name=dish_name,
        food_items=[
            FoodItemTranslation(
                food_item_id=meal.nutrition.food_items[0].id, name=ingredient
            )
        ],
        meal_instruction=[],
        meal_ingredients=[ingredient],
    )


@pytest_asyncio.fixture
async def app_day(async_session_factory, pg_session, monkeypatch):
    """A user's morning: three meals, Vietnamese rows, a hosted-style cache."""
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    cache = CacheService(redis_client=_MemoryRedis(), enabled=True)
    # Register the purge exactly as API startup does, outside development.
    monkeypatch.setattr(base_dependencies.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        base_dependencies.settings, "REDIS_URL", "rediss://default:pw@upstash.io:6379"
    )
    monkeypatch.setattr(base_dependencies, "get_cache_service", lambda: cache)
    base_dependencies._register_write_cache_invalidation_hooks()

    user_id = str(uuid4())
    local_now = datetime.now(get_zone_info(TZ))
    morning = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
    at = lambda minutes: (morning + timedelta(minutes=minutes)).astimezone(UTC)  # noqa: E731
    label = _meal(
        user_id,
        at(7),
        "Vietnamese food product",
        _item("Ingredient", 50, 1.0, 5.0, 1.3),
    )
    kiri = _meal(
        user_id,
        at(17),
        "Phô Mai Viên Kiri",
        _item("Cream cheese", 20, 0.3, 3.4, 0.3),
        raw={
            "dish_name": "Cream cheese cubes",
            "localized_language": "vi",
            "localized_dish_name": "Phô Mai Viên Kiri",
            "foods": [{"name": "Cream cheese", "localized_name": "Phô mai kem"}],
        },
    )
    banh_mi = _meal(user_id, at(20), "Sandwich", _item("Bread roll", 65, 1.3, 6.9, 0.9))
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
        for meal in (label, kiri, banh_mi):
            await uow.meals.save(meal)
        await uow.meal_translations.save(
            _vi_row(label, "Sản phẩm thực phẩm Việt Nam", "Nguyên liệu")
        )
        await uow.meal_translations.save(_vi_row(banh_mi, "Bánh mì kẹp", "Bánh mì"))
        await uow.commit()

    bus = PyMediatorEventBus()
    await SaveUserOnboardingCommandHandler(
        uow_factory=AsyncUnitOfWork,
        environment="test",
        event_publisher=MagicMock(publish=AsyncMock()),
    ).handle(
        SaveUserOnboardingCommand(
            user_id=user_id,
            age=30,
            gender="male",
            height_cm=172,
            weight_kg=80,
            job_type="desk",
            training_days_per_week=3,
            training_minutes_per_session=45,
            fitness_goal="cut",
        )
    )
    bus.register_handler(
        EditMealCommand,
        EditMealCommandHandler(
            uow_factory=AsyncUnitOfWork,
            event_publisher=MagicMock(publish=AsyncMock()),
            environment="test",
        ),
    )
    bus.register_handler(GetMealByIdQuery, GetMealByIdQueryHandler())
    bus.register_handler(
        GetDailyActivitiesQuery, GetDailyActivitiesQueryHandler(cache_service=cache)
    )
    bus.register_handler(
        GetNutritionBulkQuery, GetNutritionBulkQueryHandler(cache_service=cache)
    )
    bus.register_handler(
        GetDailyMacrosQuery, GetDailyMacrosQueryHandler(cache_service=cache)
    )

    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)
    app.include_router(meals_router)
    app.include_router(activities.router)
    app.include_router(nutrition.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    app.dependency_overrides[get_cache_service] = lambda: cache
    app.dependency_overrides[get_image_store] = lambda: MagicMock()
    app.dependency_overrides[get_async_food_reference_repository] = lambda: None
    app.dependency_overrides[get_meal_translation_service] = lambda: None
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    try:
        yield (
            client,
            local_now.date().isoformat(),
            {
                "label": label,
                "kiri": kiri,
                "banh_mi": banh_mi,
            },
        )
    finally:
        await client.aclose()
        register_local_cache_invalidation_hook(None)
        register_local_hydration_cache_invalidation_hook(None)
        register_local_movement_cache_invalidation_hook(None)


async def _edit(client, meal: Meal, body: dict) -> dict:
    response = await client.put(
        f"/v1/meals/{meal.meal_id}/ingredients",
        json={
            "nutrition_contract_version": 2,
            "created_at": meal.created_at.isoformat(),
            "meal_type": "breakfast",
            "food_item_changes": [],
            **body,
        },
        headers={**VI, **V2, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _home_total(client, day: str) -> float:
    start = (datetime.fromisoformat(day) - timedelta(days=21)).date().isoformat()
    end = (datetime.fromisoformat(day) + timedelta(days=7)).date().isoformat()
    response = await client.get(
        "/v1/nutrition/bulk", params={"start": start, "end": end}, headers=VI
    )
    assert response.status_code == 200, response.text
    # No workouts today, so consumed equals food calories (what Home shows).
    return response.json()["dates"][day]["totals"]["consumed"]["calories"]


async def _today(client, day: str, headers=VI) -> dict[str, dict]:
    response = await client.get(
        "/v1/activities/daily", params={"date": day}, headers=headers
    )
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json() if row["type"] == "meal"}


async def _name(client, meal: Meal, headers) -> str:
    response = await client.get(f"/v1/meals/{meal.meal_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["dish_name"]


@pytest.mark.asyncio
async def test_portion_change_is_what_home_and_today_read_next(app_day):
    client, day, meals = app_day
    banh_mi = meals["banh_mi"]
    total_before = await _home_total(client, day)  # Home has synced today
    row_before = (await _today(client, day))[banh_mi.meal_id]["calories"]

    edited = await _edit(
        client,
        banh_mi,
        {
            "food_item_changes": [
                {
                    "action": "update",
                    "id": banh_mi.nutrition.food_items[0].id,
                    "quantity": 15,
                    "unit": "g",
                }
            ]
        },
    )

    new_kcal = edited["meal_detail"]["total_calories"]
    assert new_kcal < row_before
    rows = await _today(client, day)
    assert rows[banh_mi.meal_id]["calories"] == pytest.approx(new_kcal, abs=0.1)
    total_after = await _home_total(client, day)
    assert total_after == pytest.approx(total_before - (row_before - new_kcal), abs=0.2)
    list_total = sum(row["calories"] for row in rows.values())
    assert total_after == pytest.approx(list_total, abs=0.2)
    macros = await client.get(
        "/v1/meals/daily/macros", params={"date": day}, headers=VI
    )
    assert macros.status_code == 200, macros.text
    assert macros.json()["food_calories"] == pytest.approx(list_total, abs=0.2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "shown", "new_name"),
    [
        ("label", "Sản phẩm thực phẩm Việt Nam", "Bánh mì của mẹ"),
        ("kiri", "Phô Mai Viên Kiri", "Phô mai sáng nay"),
    ],
)
async def test_rename_is_what_every_read_shows_next(app_day, key, shown, new_name):
    client, day, meals = app_day
    meal = meals[key]
    assert await _name(client, meal, VI) == shown
    assert (await _today(client, day))[meal.meal_id]["title"] == shown

    saved = await _edit(client, meal, {"dish_name": new_name})

    assert saved["meal_detail"]["dish_name"] == new_name
    assert await _name(client, meal, VI) == new_name  # tapping edit again
    assert await _name(client, meal, EN) == new_name
    assert (await _today(client, day))[meal.meal_id]["title"] == new_name

    await _edit(
        client,
        meal,
        {
            "food_item_changes": [
                {
                    "action": "update",
                    "id": meal.nutrition.food_items[0].id,
                    "quantity": 10,
                    "unit": "g",
                }
            ]
        },
    )
    assert await _name(client, meal, VI) == new_name

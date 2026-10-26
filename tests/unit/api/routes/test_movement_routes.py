from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.v1.movement import router


def test_movement_catalog_route_returns_activities(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_user_id():
        return "user-1"

    class FakeBus:
        async def send(self, query):
            return {"activities": [{"id": "badminton", "met": {"moderate": 7.0}}]}

    from src.api.dependencies.auth import get_current_user_id
    from src.api.dependencies.event_bus import get_configured_event_bus

    app.dependency_overrides[get_current_user_id] = fake_user_id
    app.dependency_overrides[get_configured_event_bus] = lambda: FakeBus()

    response = TestClient(app).get("/v1/movement/catalog")

    assert response.status_code == 200
    assert response.json()["activities"][0]["id"] == "badminton"


def _describe_app(monkeypatch, *, subscription=None):
    """The movement router running the real describe handler on an in-memory store."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from slowapi.errors import RateLimitExceeded

    from src.api.base_dependencies import (
        get_describe_log_guard,
        get_read_only_uow_factory,
    )
    from src.api.dependencies.auth import get_current_user_id
    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.api.exception_handlers import register_exception_handlers
    from src.api.middleware.rate_limit import (
        limiter,
        rate_limit_exceeded_handler,
        reset_limiter_counters,
    )
    from src.app.handlers.command_handlers.describe_movement_command_handler import (
        DescribeMovementCommandHandler,
    )
    from src.app.services.describe_log_guard import DescribeLogGuard
    from tests.fixtures.fakes.fake_describe_db import FakeDescribeDb

    reset_limiter_counters()
    db = FakeDescribeDb()
    generation = AsyncMock()
    generation.generate_meal_plan_async = AsyncMock(
        return_value={
            "items": [{"catalog_id": "running", "name": "chạy", "duration_min": 30}]
        }
    )
    handler = DescribeMovementCommandHandler(
        uow_factory=db.uow,
        meal_generation_service=generation,
        event_publisher=AsyncMock(),
    )

    class Bus:
        def __init__(self):
            self.commands = []

        async def send(self, command):
            self.commands.append(command)
            return await handler.handle(command)

    bus = Bus()

    async def count(key, ttl):
        return 1

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    app.dependency_overrides[get_read_only_uow_factory] = lambda: db.uow
    app.dependency_overrides[get_describe_log_guard] = lambda: DescribeLogGuard(
        enabled=True,
        daily_limit=60,
        require_subscription=subscription or AsyncMock(return_value=None),
        increment_counter=count,
    )
    return SimpleNamespace(
        client=TestClient(app), bus=bus, db=db, generation=generation
    )


def test_movement_describe_same_key_saves_once(monkeypatch):
    env = _describe_app(monkeypatch)
    headers = {"Idempotency-Key": "sentence-1"}

    first = env.client.post(
        "/v1/movement/describe",
        json={"text": "chạy 30 phút", "weight_kg": 62},
        headers=headers,
    )
    # A resend priced with a newer weight is still the same sentence.
    second = env.client.post(
        "/v1/movement/describe",
        json={"text": "chạy 30 phút", "weight_kg": 64},
        headers=headers,
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["entries"][0]["kcal_burned"] == 244.125
    assert len(env.db.state.entries) == 1
    env.generation.generate_meal_plan_async.assert_awaited_once()
    assert env.bus.commands[0].text == "chạy 30 phút"
    assert env.bus.commands[0].idempotency_key == "sentence-1"


def test_movement_describe_non_premium_gives_402(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    refuse = AsyncMock(
        side_effect=HTTPException(
            status_code=402, detail={"error_code": "SUBSCRIPTION_REQUIRED"}
        )
    )
    env = _describe_app(monkeypatch, subscription=refuse)

    response = env.client.post(
        "/v1/movement/describe",
        json={"text": "chạy 30 phút"},
        headers={"Idempotency-Key": "sentence-1"},
    )

    assert response.status_code == 402
    assert response.json()["detail"]["error_code"] == "SUBSCRIPTION_REQUIRED"
    assert env.bus.commands == []


def test_movement_describe_accepts_an_out_of_range_weight(monkeypatch):
    """The handler clamps the weight; the sentence is not rejected."""
    env = _describe_app(monkeypatch)

    response = env.client.post(
        "/v1/movement/describe",
        json={"text": "chạy 30 phút", "weight_kg": 20},
        headers={"Idempotency-Key": "sentence-1"},
    )

    assert response.status_code == 200
    assert env.bus.commands[0].weight_kg == 20
    assert response.json()["entries"][0]["kcal_burned"] == 7.5 * 3.5 * 30 * 30 / 200

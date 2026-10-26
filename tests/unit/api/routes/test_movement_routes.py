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
    """The movement router with a fake bus, a real guard and an in-memory replay store."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from slowapi.errors import RateLimitExceeded

    from src.api.base_dependencies import get_describe_log_guard
    from src.api.dependencies.auth import get_current_user_id
    from src.api.dependencies.event_bus import get_configured_event_bus
    from src.api.exception_handlers import register_exception_handlers
    from src.api.middleware.rate_limit import (
        limiter,
        rate_limit_exceeded_handler,
        reset_limiter_counters,
    )
    from src.api.services import describe_log_requests
    from src.app.services.describe_log_guard import DescribeLogGuard
    from src.infra.services.durable_write_service import DurableWriteRecord

    reset_limiter_counters()
    rows: dict = {}

    async def begin(*, user_id, action, idempotency_key, request_fingerprint):
        row = rows.setdefault((user_id, action, idempotency_key), None)
        return DurableWriteRecord(request_fingerprint, 200, row, None) if row else None

    async def complete(*, user_id, action, idempotency_key, response_body, **_):
        rows[(user_id, action, idempotency_key)] = response_body
        return DurableWriteRecord("", 200, response_body, None)

    monkeypatch.setattr(describe_log_requests, "begin_durable_write", begin)
    monkeypatch.setattr(describe_log_requests, "complete_durable_write", complete)
    monkeypatch.setattr(describe_log_requests, "abandon_durable_write", AsyncMock())

    class FakeBus:
        def __init__(self):
            self.commands = []

        async def send(self, command):
            self.commands.append(command)
            return {
                "entries": [
                    {
                        "id": f"mvmt_{len(self.commands)}",
                        "activity_id": "running",
                        "activity_name": "Running",
                        "duration_min": 30,
                        "kcal_burned": 244.125,
                        "intensity": "moderate",
                        "source": "manual",
                        "include_in_balance": True,
                        "logged_at": "2026-10-26T11:00:00Z",
                    }
                ]
            }

    bus = FakeBus()

    async def count(key, ttl):
        return 1

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    app.dependency_overrides[get_describe_log_guard] = lambda: DescribeLogGuard(
        enabled=True,
        daily_limit=60,
        require_subscription=subscription or AsyncMock(return_value=None),
        increment_counter=count,
    )
    return SimpleNamespace(client=TestClient(app), bus=bus)


def test_movement_describe_same_key_saves_once(monkeypatch):
    env = _describe_app(monkeypatch)
    body = {"text": "chạy 30 phút", "weight_kg": 62}
    headers = {"Idempotency-Key": "sentence-1"}

    first = env.client.post("/v1/movement/describe", json=body, headers=headers)
    second = env.client.post("/v1/movement/describe", json=body, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["entries"][0]["kcal_burned"] == 244.125
    assert len(env.bus.commands) == 1
    assert env.bus.commands[0].weight_kg == 62
    assert env.bus.commands[0].text == "chạy 30 phút"


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

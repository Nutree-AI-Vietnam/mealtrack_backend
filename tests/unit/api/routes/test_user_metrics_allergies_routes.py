"""GET /v1/user-profiles/metrics returns allergies and health_conditions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import user_profiles as user_profiles_mod

_METRICS = {
    "user_id": "user_1",
    "age": 30,
    "gender": "female",
    "height_cm": 160.0,
    "weight_kg": 55.0,
    "job_type": "desk",
    "training_days_per_week": 3,
    "training_minutes_per_session": 45,
    "fitness_goal": "cut",
    "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
}


class _Bus:
    def __init__(self, result: dict):
        self._result = result

    async def send(self, message):
        return self._result


@pytest.fixture
def get_metrics():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(user_profiles_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user_1"

    def fetch(**fields) -> dict:
        bus = _Bus({**_METRICS, **fields})
        app.dependency_overrides[get_configured_event_bus] = lambda: bus
        response = TestClient(app).get("/v1/user-profiles/metrics")
        assert response.status_code == 200
        return response.json()

    yield fetch
    app.dependency_overrides.clear()


def test_saved_allergies_and_conditions_reach_the_response(get_metrics):
    body = get_metrics(allergies=["tôm", "milk"], health_conditions=["pregnancy"])

    assert body["allergies"] == ["tôm", "milk"]
    assert body["health_conditions"] == ["pregnancy"]


def test_both_keys_are_present_as_empty_lists_when_none_are_saved(get_metrics):
    """My Plan shows the cards only when GET metrics carries these keys."""
    body = get_metrics()

    assert body["allergies"] == []
    assert body["health_conditions"] == []

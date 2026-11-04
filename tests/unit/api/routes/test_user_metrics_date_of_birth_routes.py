"""date_of_birth on GET and POST /v1/user-profiles/metrics."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import user_profiles as user_profiles_mod
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand

_METRICS = {
    "user_id": "user_1",
    "age": 30,
    "gender": "female",
    "height_cm": 160.0,
    "weight_kg": 55.0,
    "job_type": "desk",
    "training_days_per_week": 3,
    "training_minutes_per_session": 45,
    "fitness_goal": "recomp",
    "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
}
_TDEE = {
    "bmr": 1300.0,
    "tdee": 1900.0,
    "profile_data": {"fitness_goal": "recomp"},
    "macros": {"calories": 1900.0, "protein": 110.0, "carbs": 220.0, "fat": 60.0},
    "activity_multiplier": 1.45,
    "formula_used": "Mifflin-St Jeor",
    "is_custom": False,
}


class _Bus:
    def __init__(self, reply):
        self.sent = []
        self._reply = reply

    async def send(self, message):
        self.sent.append(message)
        return self._reply(message)


@pytest.fixture
def api():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(user_profiles_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user_1"

    def client_for(bus: _Bus) -> TestClient:
        app.dependency_overrides[get_configured_event_bus] = lambda: bus
        return TestClient(app)

    yield client_for
    app.dependency_overrides.clear()


def _metrics_bus(**fields) -> _Bus:
    return _Bus(lambda _: {**_METRICS, **fields})


def _update_bus() -> _Bus:
    return _Bus(
        lambda message: None if isinstance(message, UpdateUserMetricsCommand) else _TDEE
    )


def test_get_returns_the_stored_date_of_birth(api):
    response = api(_metrics_bus(date_of_birth="1996-03-01")).get(
        "/v1/user-profiles/metrics"
    )

    assert response.status_code == 200
    assert response.json()["date_of_birth"] == "1996-03-01"


@pytest.mark.parametrize(
    "stored", [{"date_of_birth": None}, {}], ids=["null", "absent"]
)
def test_get_keeps_the_key_when_no_date_is_stored(api, stored):
    """The app sends date_of_birth only to a server whose GET carries the key."""
    response = api(_metrics_bus(**stored)).get("/v1/user-profiles/metrics")

    body = response.json()
    assert response.status_code == 200
    assert "date_of_birth" in body
    assert body["date_of_birth"] is None


def test_post_passes_the_date_of_birth_to_the_update(api):
    bus = _update_bus()
    born = date(date.today().year - 30, 1, 1)

    response = api(bus).post(
        "/v1/user-profiles/metrics", json={"date_of_birth": born.isoformat()}
    )

    assert response.status_code == 200
    command = next(m for m in bus.sent if isinstance(m, UpdateUserMetricsCommand))
    assert command.date_of_birth == born


@pytest.mark.parametrize(
    "date_of_birth",
    [
        (date.today() + timedelta(days=1)).isoformat(),
        "2001-02-30",
        "not-a-date",
        date(date.today().year - 5, 1, 1).isoformat(),
        date(date.today().year - 125, 1, 1).isoformat(),
    ],
    ids=["future", "impossible", "malformed", "under-12", "over-120"],
)
def test_post_rejects_a_date_of_birth_outside_the_contract(api, date_of_birth):
    bus = _update_bus()

    response = api(bus).post(
        "/v1/user-profiles/metrics", json={"date_of_birth": date_of_birth}
    )

    assert response.status_code == 422
    assert bus.sent == []

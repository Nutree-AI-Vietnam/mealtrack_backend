"""GET/PUT /v1/notifications/preferences carry the per-meal, water and engagement fields."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import notifications as notifications_mod
from src.app.commands.notification import UpdateNotificationPreferencesCommand
from src.app.queries.notification import GetNotificationPreferencesQuery
from src.domain.model.notification import NotificationPreferences

USER_ID = "00000000-0000-0000-0000-0000000000b2"
TOGGLES = (
    "streak_reminders_enabled",
    "reengagement_enabled",
    "progress_updates_enabled",
    "offers_enabled",
)


class _RecordingBus:
    """Answers with the real domain model so the response reflects its rules."""

    def __init__(self) -> None:
        self.prefs = NotificationPreferences.create_default(USER_ID)
        self.sent: list[Any] = []

    async def send(self, message: Any) -> dict[str, Any]:
        self.sent.append(message)
        if isinstance(message, GetNotificationPreferencesQuery):
            return self.prefs.to_dict()
        assert isinstance(message, UpdateNotificationPreferencesCommand)
        self.prefs = self.prefs.update_preferences(
            meal_reminders_enabled=message.meal_reminders_enabled,
            breakfast_reminder_enabled=message.breakfast_reminder_enabled,
            lunch_reminder_enabled=message.lunch_reminder_enabled,
            dinner_reminder_enabled=message.dinner_reminder_enabled,
            hydration_reminder_time_minutes=message.hydration_reminder_time_minutes,
            daily_summary_time_minutes=message.daily_summary_time_minutes,
            **{name: getattr(message, name) for name in TOGGLES},
        )
        return {"success": True, "preferences": self.prefs.to_dict()}


@pytest.fixture
def api():
    bus = _RecordingBus()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(notifications_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    yield TestClient(app), bus
    app.dependency_overrides.clear()


def _toggles(body: dict[str, Any]) -> dict[str, Any]:
    return {name: body[name] for name in TOGGLES}


def test_get_returns_meal_switches_and_water_time(api):
    client, _ = api

    response = client.get("/v1/notifications/preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["breakfast_reminder_enabled"] is True
    assert body["lunch_reminder_enabled"] is True
    assert body["dinner_reminder_enabled"] is True
    assert body["meal_reminders_enabled"] is True
    assert body["hydration_reminder_time_minutes"] == 930


def test_put_passes_new_fields_to_the_command(api):
    client, bus = api

    response = client.put(
        "/v1/notifications/preferences",
        json={"dinner_reminder_enabled": False, "hydration_reminder_time_minutes": 840},
    )

    assert response.status_code == 200
    command = bus.sent[-1]
    assert command.user_id == USER_ID
    assert command.dinner_reminder_enabled is False
    assert command.breakfast_reminder_enabled is None
    assert command.hydration_reminder_time_minutes == 840
    preferences = response.json()["preferences"]
    assert preferences["dinner_reminder_enabled"] is False
    assert preferences["meal_reminders_enabled"] is True
    assert preferences["hydration_reminder_time_minutes"] == 840


def test_put_from_an_older_app_leaves_a_switched_off_meal_off(api):
    client, _ = api
    client.put("/v1/notifications/preferences", json={"lunch_reminder_enabled": False})

    response = client.put(
        "/v1/notifications/preferences",
        json={"meal_reminders_enabled": True, "daily_summary_time_minutes": 1200},
    )

    assert response.status_code == 200
    preferences = response.json()["preferences"]
    assert preferences["lunch_reminder_enabled"] is False
    assert preferences["meal_reminders_enabled"] is True


@pytest.mark.parametrize("minutes", [-1, 1440])
def test_put_rejects_a_water_time_outside_the_day(api, minutes: int):
    client, bus = api

    response = client.put(
        "/v1/notifications/preferences",
        json={"hydration_reminder_time_minutes": minutes},
    )

    assert response.status_code == 422
    assert bus.sent == []


def test_get_returns_the_toggle_defaults(api):
    client, _ = api

    response = client.get("/v1/notifications/preferences")

    assert response.status_code == 200
    assert _toggles(response.json()) == {
        "streak_reminders_enabled": True,
        "reengagement_enabled": True,
        "progress_updates_enabled": True,
        "offers_enabled": False,
    }


def test_put_passes_every_toggle_to_the_command(api):
    client, bus = api
    body = {
        "streak_reminders_enabled": False,
        "reengagement_enabled": False,
        "progress_updates_enabled": False,
        "offers_enabled": True,
    }

    response = client.put("/v1/notifications/preferences", json=body)

    assert response.status_code == 200
    [command] = bus.sent
    assert isinstance(command, UpdateNotificationPreferencesCommand)
    assert command.user_id == USER_ID
    assert {name: getattr(command, name) for name in TOGGLES} == body
    assert response.json()["success"] is True
    assert _toggles(response.json()["preferences"]) == body


def test_put_without_the_toggles_leaves_them_unchanged(api):
    client, bus = api
    bus.prefs = bus.prefs.update_preferences(
        reengagement_enabled=False, offers_enabled=True
    )

    response = client.put(
        "/v1/notifications/preferences", json={"meal_reminders_enabled": False}
    )

    assert response.status_code == 200
    [command] = bus.sent
    assert all(getattr(command, name) is None for name in TOGGLES)
    assert _toggles(response.json()["preferences"]) == {
        "streak_reminders_enabled": True,
        "reengagement_enabled": False,
        "progress_updates_enabled": True,
        "offers_enabled": True,
    }


@pytest.mark.parametrize("toggle", TOGGLES)
def test_put_rejects_a_toggle_that_is_not_a_boolean(api, toggle):
    client, bus = api

    response = client.put("/v1/notifications/preferences", json={toggle: "maybe"})

    assert response.status_code == 422
    assert bus.sent == []

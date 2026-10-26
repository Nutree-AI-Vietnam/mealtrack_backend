"""GET/PUT /v1/notifications/preferences carry the engagement toggles."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import notifications as notifications_mod
from src.app.commands.notification import UpdateNotificationPreferencesCommand
from src.domain.model.notification import NotificationPreferences

USER_ID = str(uuid4())
TOGGLES = (
    "streak_reminders_enabled",
    "reengagement_enabled",
    "progress_updates_enabled",
    "offers_enabled",
)


class _FakeBus:
    """Applies a preferences update the way the real handler does."""

    def __init__(self, stored: NotificationPreferences) -> None:
        self.stored = stored
        self.sent: list[Any] = []

    async def send(self, message: Any) -> dict[str, Any]:
        self.sent.append(message)
        if isinstance(message, UpdateNotificationPreferencesCommand):
            self.stored = self.stored.update_preferences(
                **{name: getattr(message, name) for name in TOGGLES}
            )
            return {"success": True, "preferences": self.stored.to_dict()}
        return self.stored.to_dict()


@pytest.fixture
def bus() -> _FakeBus:
    return _FakeBus(NotificationPreferences.create_default(USER_ID))


@pytest.fixture
def client(bus):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(notifications_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    yield TestClient(app)
    app.dependency_overrides.clear()


def _toggles(body: dict[str, Any]) -> dict[str, Any]:
    return {name: body[name] for name in TOGGLES}


def test_get_returns_the_toggle_defaults(client):
    response = client.get("/v1/notifications/preferences")

    assert response.status_code == 200
    assert _toggles(response.json()) == {
        "streak_reminders_enabled": True,
        "reengagement_enabled": True,
        "progress_updates_enabled": True,
        "offers_enabled": False,
    }


def test_put_passes_every_toggle_to_the_command(client, bus):
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


def test_put_without_the_toggles_leaves_them_unchanged(client, bus):
    bus.stored = bus.stored.update_preferences(
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
def test_put_rejects_a_toggle_that_is_not_a_boolean(client, bus, toggle):
    response = client.put("/v1/notifications/preferences", json={toggle: "maybe"})

    assert response.status_code == 422
    assert bus.sent == []

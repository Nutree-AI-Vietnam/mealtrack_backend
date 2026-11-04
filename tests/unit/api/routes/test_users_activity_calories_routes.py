"""GET and PATCH /v1/users/activity-calories through the real handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import users as users_mod
from src.app.commands.user import UpdateActivityCaloriesCommand
from src.app.handlers.command_handlers.update_activity_calories_command_handler import (
    UpdateActivityCaloriesCommandHandler,
)
from src.app.handlers.query_handlers.get_activity_calories_query_handler import (
    GetActivityCaloriesQueryHandler,
)
from src.app.queries.user import GetActivityCaloriesQuery
from src.infra.event_bus import PyMediatorEventBus

_USER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def api():
    uow = FakeUnitOfWork()
    purge = AsyncMock()
    bus = PyMediatorEventBus()
    bus.register_handler(
        GetActivityCaloriesQuery, GetActivityCaloriesQueryHandler(lambda: uow)
    )
    bus.register_handler(
        UpdateActivityCaloriesCommand,
        UpdateActivityCaloriesCommandHandler(
            lambda: uow, event_publisher=AsyncMock(), purge_caches=purge
        ),
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(users_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    yield TestClient(app), uow, purge
    app.dependency_overrides.clear()


def test_get_defaults_to_enabled(api):
    client, _, _ = api

    response = client.get("/v1/users/activity-calories")

    assert response.status_code == 200
    assert response.json() == {"enabled": True}


def test_patch_turns_it_off_and_get_reads_it_back(api):
    client, uow, purge = api

    patched = client.patch("/v1/users/activity-calories", json={"enabled": False})
    read_back = client.get("/v1/users/activity-calories")

    assert patched.status_code == 200
    assert patched.json() == {"enabled": False}
    assert read_back.json() == {"enabled": False}
    assert uow.users.activity_calories[_USER_ID] is False
    purge.assert_awaited_once()


def test_patch_turns_it_back_on(api):
    client, _, _ = api

    client.patch("/v1/users/activity-calories", json={"enabled": False})
    patched = client.patch("/v1/users/activity-calories", json={"enabled": True})

    assert patched.json() == {"enabled": True}
    assert client.get("/v1/users/activity-calories").json() == {"enabled": True}


def test_patch_without_a_boolean_is_rejected(api):
    client, uow, purge = api

    response = client.patch("/v1/users/activity-calories", json={})

    assert response.status_code == 422
    assert uow.users.activity_calories == {}
    purge.assert_not_awaited()

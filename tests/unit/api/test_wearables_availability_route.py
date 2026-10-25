"""Which wearables can sign in, sync health, and disconnecting.

Open Wearables enables every provider and hands out a link with an empty
client id when no app is registered. The backend only offers providers in
OPEN_WEARABLES_PROVIDERS and refuses a link without a client id.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.base_dependencies import get_open_wearables_account_links
from src.api.dependencies.auth import get_current_user_id
from src.api.routes.v1 import wearables as wearables_mod
from src.infra.adapters.open_wearables_client import (
    OpenWearablesClient,
    OpenWearablesError,
)
from src.infra.config.settings import settings


def _client(ow_client: OpenWearablesClient | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(wearables_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    # Tests that need a linked account replace _linked_user_id itself.
    app.dependency_overrides[get_open_wearables_account_links] = lambda: None
    if ow_client is not None:
        app.dependency_overrides[wearables_mod.open_wearables_client] = lambda: (
            ow_client
        )
    return TestClient(app)


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    monkeypatch.setattr(settings, "OPEN_WEARABLES_PROVIDERS", "google_health")


@pytest.fixture
def linked(monkeypatch):
    async def _linked(_accounts, _user_id):
        return "ow-user"

    monkeypatch.setattr(wearables_mod, "_linked_user_id", _linked)


class _Unreachable(OpenWearablesClient):
    def __init__(self) -> None:
        super().__init__("http://ow.test", "test-key")

    async def _send(self, *args, **kwargs):  # type: ignore[override]
        raise AssertionError("Open Wearables must not be called")


def test_providers_marks_only_registered_apps_available(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    monkeypatch.setattr(
        settings, "OPEN_WEARABLES_PROVIDERS", " Google_Health, garmin ,fitbit,,"
    )
    response = _client().get("/v1/wearables/providers")
    assert response.status_code == 200
    available = {
        row["provider"]: row["available"] for row in response.json()["providers"]
    }
    assert available["google_health"] is True
    assert available["garmin"] is True
    assert available["oura"] is False
    assert available["strava"] is False
    assert "fitbit" not in available
    assert set(available) == wearables_mod.CLOUD_WEARABLE_PROVIDERS


def test_providers_are_all_unavailable_until_sign_in_is_configured(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: False)
    monkeypatch.setattr(settings, "OPEN_WEARABLES_PROVIDERS", "google_health")
    response = _client().get("/v1/wearables/providers")
    assert response.status_code == 200
    assert not any(row["available"] for row in response.json()["providers"])


def test_authorize_refuses_a_provider_without_a_registered_app(ready, linked):
    response = _client(_Unreachable()).get("/v1/wearables/oauth/garmin/authorize")
    assert response.status_code == 409
    assert response.json()["detail"] == "This wearable is not available yet"


@pytest.mark.parametrize(
    "link",
    [
        "https://connect.garmin.com/oauth2Confirm?response_type=code&client_id=",
        "https://cloud.ouraring.com/oauth/authorize?response_type=code",
        "https://www.strava.com/oauth/authorize?client_id=%20",
    ],
)
def test_authorize_refuses_a_link_without_a_client_id(ready, linked, link):
    class _Client(OpenWearablesClient):
        async def authorization_url(self, provider, user_id, redirect_uri):
            return link

    response = _client(_Client("http://ow.test", "test-key")).get(
        "/v1/wearables/oauth/google_health/authorize"
    )
    assert response.status_code == 409


def test_in_app_sign_in_returns_through_the_wearables_auth_host(ready, linked):
    seen: list[str] = []

    class _Client(OpenWearablesClient):
        async def authorization_url(self, provider, user_id, redirect_uri):
            seen.append(redirect_uri)
            return "https://accounts.google.com/o/oauth2/v2/auth?client_id=app"

    response = _client(_Client("http://ow.test", "test-key")).get(
        "/v1/wearables/oauth/google_health/authorize",
        params={"return_to": "session"},
    )
    assert response.status_code == 200
    assert seen == ["nutree://wearables-auth/google_health"]


def test_authorize_rejects_an_unknown_return_target(ready, linked):
    response = _client(_Unreachable()).get(
        "/v1/wearables/oauth/google_health/authorize",
        params={"return_to": "https://evil.example"},
    )
    assert response.status_code == 422


def test_connections_carry_the_newest_sync_outcome(ready, linked):
    class _Client(OpenWearablesClient):
        async def connections(self, user_id):
            return [
                {"provider": "google_health", "status": "active"},
                {"provider": "strava", "status": "active"},
            ]

        async def sync_states(self, user_id):
            return {"google_health": {"sync_status": "failing", "last_synced_at": None}}

    response = _client(_Client("http://ow.test", "test-key")).get(
        "/v1/wearables/connections"
    )
    assert response.status_code == 200
    assert response.json() == {
        "connections": [
            {
                "provider": "google_health",
                "status": "active",
                "sync_status": "failing",
                "last_synced_at": None,
            },
            {
                "provider": "strava",
                "status": "active",
                "sync_status": "unknown",
                "last_synced_at": None,
            },
        ]
    }


def test_connections_still_load_when_sync_runs_fail(ready, linked):
    class _Client(OpenWearablesClient):
        async def connections(self, user_id):
            return [{"provider": "google_health", "status": "active"}]

        async def sync_states(self, user_id):
            raise OpenWearablesError("sync runs down")

    response = _client(_Client("http://ow.test", "test-key")).get(
        "/v1/wearables/connections"
    )
    assert response.status_code == 200
    row = response.json()["connections"][0]
    assert row["status"] == "active"
    assert row["sync_status"] == "unknown"


def test_disconnect_revokes_through_open_wearables(ready, linked):
    seen: list[tuple[str, str]] = []

    class _Client(OpenWearablesClient):
        async def disconnect(self, user_id, provider):
            seen.append((user_id, provider))
            return True

    response = _client(_Client("http://ow.test", "test-key")).delete(
        "/v1/wearables/connections/google_health"
    )
    assert response.status_code == 204
    assert seen == [("ow-user", "google_health")]


def test_disconnect_without_a_linked_account_succeeds(ready, monkeypatch):
    async def _not_linked(_accounts, _user_id):
        return None

    monkeypatch.setattr(wearables_mod, "_linked_user_id", _not_linked)
    response = _client(_Unreachable()).delete("/v1/wearables/connections/oura")
    assert response.status_code == 204


def test_disconnect_reports_an_open_wearables_failure(ready, linked):
    class _Client(OpenWearablesClient):
        async def disconnect(self, user_id, provider):
            raise OpenWearablesError("down")

    response = _client(_Client("http://ow.test", "test-key")).delete(
        "/v1/wearables/connections/google_health"
    )
    assert response.status_code == 502


def test_disconnect_rejects_phone_sources(ready, linked):
    response = _client(_Unreachable()).delete("/v1/wearables/connections/apple")
    assert response.status_code == 400

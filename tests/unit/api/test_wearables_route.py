"""Open Wearables client and route guards.

Activity reads use one provider. They do not add sources together.
Ultrahuman supplies steps and drops calorie burn. Strava workouts are listed
for the person to add, and never fill the daily balance.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.base_dependencies import get_open_wearables_account_links
from src.api.dependencies.auth import get_current_user_id
from src.api.routes.v1 import wearables as wearables_mod
from src.infra.adapters.open_wearables_client import (
    OpenWearablesClient,
    choose_activity_row,
)
from src.infra.config.settings import settings


def test_cloud_catalog_excludes_phone_sources_and_fitbit():
    assert "garmin" in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "strava" in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "apple" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "health_connect" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "fitbit" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "samsung" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "strava" not in wearables_mod.BALANCE_PROVIDERS
    assert "ultrahuman" in wearables_mod.STEPS_PROVIDERS
    assert "strava" in wearables_mod.WORKOUT_PROVIDERS


def test_choose_activity_row_uses_one_provider_and_does_not_sum():
    rows = [
        {
            "source": {"provider": "apple"},
            "steps": 9000,
            "active_calories_kcal": 400,
            "total_calories_kcal": 2200,
        },
        {
            "source": {"provider": "garmin"},
            "steps": 1000,
            "active_calories_kcal": 80,
            "total_calories_kcal": 1900,
        },
        {
            "source": {"provider": "garmin"},
            "steps": 4200,
            "active_calories_kcal": 210,
            "total_calories_kcal": 2400,
        },
    ]
    chosen = choose_activity_row(rows, "garmin")
    assert chosen is not None
    assert chosen["steps"] == 4200
    assert chosen["active_calories_kcal"] == 210


@pytest.mark.asyncio
async def test_activity_day_reads_the_chosen_provider_samples():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.headers["X-Open-Wearables-API-Key"] == "test-key"
        assert "workouts" not in request.url.path
        assert "meals" not in request.url.path
        assert request.url.params.get("provider") == "garmin"
        assert "workouts" not in request.url.params.get("types", "")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"type": "steps", "value": 5000, "is_daily_total": True},
                    {"type": "active_energy", "value": 180.5, "is_daily_total": True},
                    {"type": "steps", "value": 999, "is_daily_total": False},
                ],
                "pagination": {"has_more": False},
            },
        )

    client = OpenWearablesClient(
        "http://ow.test/api/v1",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    steps, active = await client.activity_day("user-1", "2026-10-12", "garmin")
    assert steps == 5000
    assert active == 180.5
    assert seen == ["/api/v1/users/user-1/timeseries"]


@pytest.mark.asyncio
async def test_workouts_on_day_keeps_name_and_calories():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("provider") == "strava"
        assert "meals" not in request.url.path
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "ride-1",
                        "name": "Morning Ride",
                        "duration_seconds": 2700,
                        "calories_kcal": 420,
                    },
                    {"name": "missing id"},
                ],
                "pagination": {"has_more": False},
            },
        )

    client = OpenWearablesClient(
        "http://ow.test",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    rows = await client.workouts_on_day("user-1", "2026-10-13", "strava")
    assert rows == [
        {
            "id": "ride-1",
            "name": "Morning Ride",
            "duration_min": 45,
            "calories_kcal": 420.0,
        }
    ]


@pytest.mark.asyncio
async def test_authorization_url_uses_api_key_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Open-Wearables-API-Key"] == "test-key"
        assert (
            request.url.params["redirect_uri"]
            == "nutree://open-nutree/wearables/connected/garmin"
        )
        return httpx.Response(
            200,
            json={
                "authorization_url": "https://connect.garmin.com/start",
                "state": "s",
            },
        )

    client = OpenWearablesClient(
        "http://ow.test",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    url = await client.authorization_url(
        "garmin",
        "ow-user",
        "nutree://open-nutree/wearables/connected/garmin",
    )
    assert url == "https://connect.garmin.com/start"


class _Accounts:
    """Stored links; a second link keeps the first stored id."""

    def __init__(self, stored: str | None = None):
        self.stored = stored
        self.linked: list[tuple[str, str]] = []

    async def linked_user_id(self, user_id):
        return self.stored

    async def link(self, user_id, open_wearables_user_id):
        self.linked.append((user_id, open_wearables_user_id))
        if self.stored is None:
            self.stored = open_wearables_user_id
        return self.stored


def _client(
    ow_client: OpenWearablesClient | None = None,
    accounts: _Accounts | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(wearables_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_open_wearables_account_links] = lambda: (
        accounts or _Accounts()
    )
    if ow_client is not None:
        app.dependency_overrides[wearables_mod.open_wearables_client] = lambda: (
            ow_client
        )
    return TestClient(app)


def test_phone_provider_is_rejected(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    response = _client().get("/v1/wearables/oauth/apple/authorize")
    assert response.status_code == 400


def test_unconfigured_sign_in_conflicts(monkeypatch):
    monkeypatch.setattr(settings, "OPEN_WEARABLES_API_URL", "")
    monkeypatch.setattr(settings, "OPEN_WEARABLES_API_KEY", "")
    monkeypatch.setattr(settings, "OPEN_WEARABLES_APP_ID", "")
    monkeypatch.setattr(settings, "OPEN_WEARABLES_APP_SECRET", "")
    response = _client().get("/v1/wearables/oauth/garmin/authorize")
    assert response.status_code == 409


def test_sign_in_returns_through_the_link_android_opens(monkeypatch):
    # Android's nutree intent filter requires the open-nutree host.
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    monkeypatch.setattr(settings, "OPEN_WEARABLES_PROVIDERS", "garmin")

    async def linked(_session, _user_id):
        return "ow-user"

    monkeypatch.setattr(wearables_mod, "_linked_user_id", linked)
    seen: list[tuple[str, str, str]] = []
    start = "https://connect.garmin.com/start?client_id=garmin-app"

    class _Client(OpenWearablesClient):
        async def authorization_url(self, provider, user_id, redirect_uri):
            seen.append((provider, user_id, redirect_uri))
            return start

    response = _client(_Client("http://ow.test", "test-key")).get(
        "/v1/wearables/oauth/garmin/authorize"
    )
    assert response.status_code == 200
    assert response.json() == {"authorization_url": start}
    assert seen == [
        ("garmin", "ow-user", "nutree://open-nutree/wearables/connected/garmin")
    ]


def test_first_sign_in_links_the_created_user(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    monkeypatch.setattr(settings, "OPEN_WEARABLES_PROVIDERS", "garmin")
    accounts = _Accounts()
    seen: list[str] = []

    class _Client(OpenWearablesClient):
        async def create_user(self):
            return "ow-created"

        async def authorization_url(self, provider, user_id, redirect_uri):
            seen.append(user_id)
            return "https://connect.garmin.com/start?client_id=garmin-app"

    response = _client(_Client("http://ow.test", "test-key"), accounts).get(
        "/v1/wearables/oauth/garmin/authorize"
    )
    assert response.status_code == 200
    assert accounts.linked == [("user-1", "ow-created")]
    assert seen == ["ow-created"]


def test_sign_in_race_uses_the_first_stored_user(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    monkeypatch.setattr(settings, "OPEN_WEARABLES_PROVIDERS", "garmin")

    class _RacedAccounts(_Accounts):
        async def linked_user_id(self, user_id):
            return None

        async def link(self, user_id, open_wearables_user_id):
            self.linked.append((user_id, open_wearables_user_id))
            return "ow-winner"

    accounts = _RacedAccounts()
    seen: list[str] = []

    class _Client(OpenWearablesClient):
        async def create_user(self):
            return "ow-loser"

        async def authorization_url(self, provider, user_id, redirect_uri):
            seen.append(user_id)
            return "https://connect.garmin.com/start?client_id=garmin-app"

    response = _client(_Client("http://ow.test", "test-key"), accounts).get(
        "/v1/wearables/oauth/garmin/authorize"
    )
    assert response.status_code == 200
    assert seen == ["ow-winner"]


def test_ultrahuman_steps_ignore_energy(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)

    async def linked(_session, _user_id):
        return "ow-user"

    monkeypatch.setattr(wearables_mod, "_linked_user_id", linked)

    class _Client(OpenWearablesClient):
        async def activity_day(self, user_id, day, provider):
            assert provider == "ultrahuman"
            return 2000, 200.3

    response = _client(_Client("http://ow.test", "test-key")).get(
        "/v1/wearables/activity",
        params={"date": "2026-10-13", "provider": "ultrahuman"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["steps"] == 2000
    assert body["active_calories_kcal"] == 0
    assert body["connected"] is True


def test_strava_workouts_are_listed_not_applied(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)

    async def linked(_session, _user_id):
        return "ow-user"

    monkeypatch.setattr(wearables_mod, "_linked_user_id", linked)

    class _Client(OpenWearablesClient):
        async def workouts_on_day(self, user_id, day, provider):
            assert provider == "strava"
            return [
                {
                    "id": "ride-1",
                    "name": "Morning Ride",
                    "duration_min": 45,
                    "calories_kcal": 420.0,
                }
            ]

    response = _client(_Client("http://ow.test", "test-key")).get(
        "/v1/wearables/workouts",
        params={"date": "2026-10-13", "provider": "strava"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workouts"][0]["name"] == "Morning Ride"
    assert body["workouts"][0]["calories_kcal"] == 420.0


def test_partial_providers_do_not_fill_the_balance(monkeypatch):
    monkeypatch.setattr(wearables_mod, "wearable_sign_in_ready", lambda: True)
    response = _client().get(
        "/v1/wearables/activity",
        params={"date": "2026-10-12", "provider": "strava"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["steps"] == 0
    assert body["active_calories_kcal"] == 0
    assert body["connected"] is False

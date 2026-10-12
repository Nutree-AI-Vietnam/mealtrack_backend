"""Open Wearables client and route guards.

Activity reads use active calories from one provider row. They do not add
sources together and do not request workouts, meals, or water.
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.routes.v1 import wearables as wearables_mod
from src.infra.adapters.open_wearables_client import (
    OpenWearablesClient,
    choose_activity_row,
)
from src.infra.config.settings import settings
from src.infra.database.config_async import get_async_db


def test_cloud_catalog_excludes_phone_sources_and_fitbit():
    assert "garmin" in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "strava" in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "apple" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "health_connect" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "fitbit" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "samsung" not in wearables_mod.CLOUD_WEARABLE_PROVIDERS
    assert "strava" not in wearables_mod.BALANCE_PROVIDERS


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
async def test_activity_day_requests_summary_not_workouts():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.headers["X-Open-Wearables-API-Key"] == "test-key"
        assert "workouts" not in request.url.path
        assert "meals" not in request.url.path
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "source": {"provider": "garmin"},
                        "steps": 5000,
                        "active_calories_kcal": 180.5,
                        "total_calories_kcal": 2500,
                    }
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
    assert seen == ["/api/v1/users/user-1/summaries/activity"]


@pytest.mark.asyncio
async def test_authorization_url_uses_api_key_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Open-Wearables-API-Key"] == "test-key"
        assert request.url.params["redirect_uri"] == "nutree:///wearables/connected/garmin"
        return httpx.Response(
            200,
            json={"authorization_url": "https://connect.garmin.com/start", "state": "s"},
        )

    client = OpenWearablesClient(
        "http://ow.test",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    url = await client.authorization_url(
        "garmin",
        "ow-user",
        "nutree:///wearables/connected/garmin",
    )
    assert url == "https://connect.garmin.com/start"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(wearables_mod.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"

    async def _db():
        yield None

    app.dependency_overrides[get_async_db] = _db
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

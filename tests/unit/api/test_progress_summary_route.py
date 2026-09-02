"""Route-level 422 for inverted / malformed progress summary dates."""

from datetime import date
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.progress import router


def _client(send=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    bus = AsyncMock()
    bus.send = send or AsyncMock(
        return_value={
            "effective_start": "2026-08-26",
            "effective_end": "2026-09-01",
            "cap_days": 400,
            "days": [],
        }
    )
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return TestClient(app), bus


def test_inverted_dates_return_422_without_dispatch():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"start_date": "2026-09-02", "end_date": "2026-09-01"},
    )
    assert response.status_code == 422
    bus.send.assert_not_awaited()


def test_malformed_date_returns_422():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"start_date": "not-a-date", "end_date": "2026-09-01"},
    )
    assert response.status_code == 422
    bus.send.assert_not_called()


def test_valid_range_dispatches_query():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"start_date": "2026-08-26", "end_date": "2026-09-01"},
    )
    assert response.status_code == 200
    query = bus.send.await_args.args[0]
    assert query.start_date == date(2026, 8, 26)
    assert query.end_date == date(2026, 9, 1)

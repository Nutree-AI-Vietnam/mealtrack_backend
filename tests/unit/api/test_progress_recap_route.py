from datetime import date
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.progress import router


def _client(send=None) -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    bus = AsyncMock()
    bus.send = send or AsyncMock(
        return_value={
            "status": "missing",
            "horizon": "week",
            "effective_start": "2026-09-07",
            "effective_end": "2026-09-13",
            "headline": "",
            "body": "",
            "next_move": "",
            "highlights": [],
            "generated_at": None,
        }
    )
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return TestClient(app), bus


def test_invalid_horizon_returns_422() -> None:
    client, bus = _client()
    response = client.get(
        "/v1/progress/recap",
        params={"horizon": "custom", "start_date": "2026-09-07", "end_date": "2026-09-13"},
    )
    assert response.status_code == 422
    bus.send.assert_not_awaited()


def test_get_recap_dispatches_query() -> None:
    client, bus = _client()
    response = client.get(
        "/v1/progress/recap",
        params={"horizon": "week", "start_date": "2026-09-07", "end_date": "2026-09-13"},
        headers={"Accept-Language": "vi-VN"},
    )
    assert response.status_code == 200
    query = bus.send.await_args.args[0]
    assert query.horizon == "week"
    assert query.start_date == date(2026, 9, 7)
    assert query.locale == "vi"


def test_post_recap_dispatches_command() -> None:
    client, bus = _client()
    response = client.post(
        "/v1/progress/recap",
        params={"horizon": "year", "start_date": "2026-01-01", "end_date": "2026-09-11"},
    )
    assert response.status_code == 200
    command = bus.send.await_args.args[0]
    assert command.horizon == "year"
    assert command.force is False

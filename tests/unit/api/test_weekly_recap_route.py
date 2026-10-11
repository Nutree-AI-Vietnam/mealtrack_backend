"""GET /v1/recaps/latest: the newest weekly recap, or 204 when there is none."""

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.fixtures.weekly_recap_samples import sample_stats

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.recaps import router
from src.app.queries.progress import GetLatestWeeklyRecapQuery
from src.domain.services.weekly_recap import RecapConfig, generate_recap

PAYLOAD = generate_recap(sample_stats("steady"), RecapConfig(), "vi")


def _client(result) -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    bus = AsyncMock()
    bus.send = AsyncMock(return_value=result)
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return TestClient(app), bus


def test_latest_recap_returns_the_payload() -> None:
    client, _ = _client(PAYLOAD)

    response = client.get("/v1/recaps/latest")

    assert response.status_code == 200
    assert response.json() == PAYLOAD


def test_no_recap_returns_204_without_a_body() -> None:
    client, _ = _client(None)

    response = client.get("/v1/recaps/latest")

    assert response.status_code == 204
    assert response.content == b""


def test_latest_recap_reads_the_app_language_and_timezone() -> None:
    client, bus = _client(PAYLOAD)

    client.get(
        "/v1/recaps/latest",
        headers={"Accept-Language": "vi-VN,vi;q=0.9", "X-Timezone": "Asia/Saigon"},
    )

    assert bus.send.await_args.args[0] == GetLatestWeeklyRecapQuery(
        user_id="user-1", header_timezone="Asia/Saigon", locale="vi"
    )


def test_latest_recap_without_headers_is_english() -> None:
    client, bus = _client(None)

    client.get("/v1/recaps/latest")

    query = bus.send.await_args.args[0]
    assert (query.header_timezone, query.locale) == (None, "en")

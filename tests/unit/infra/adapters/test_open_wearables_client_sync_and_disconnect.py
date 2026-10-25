"""Sync health and disconnecting through the Open Wearables client."""

import httpx
import pytest

from src.infra.adapters.open_wearables_client import (
    OpenWearablesClient,
    OpenWearablesError,
)
from src.infra.adapters.open_wearables_sync_runs import summarize_sync_runs

OW_USER_ID = "7d3f0c1e-2a4b-4c5d-8e9f-0a1b2c3d4e5f"


def _client(handler) -> OpenWearablesClient:
    return OpenWearablesClient(
        "https://wearables.example/api/v1",
        "test-key",
        transport=httpx.MockTransport(handler),
    )


def _run(provider, status, last_update, ended_at=None):
    return {
        "run_id": f"{provider}-{last_update}",
        "user_id": OW_USER_ID,
        "provider": provider,
        "source": "pull",
        "stage": "completed",
        "status": status,
        "started_at": last_update,
        "ended_at": ended_at,
        "last_update": last_update,
    }


@pytest.mark.asyncio
async def test_sync_states_reads_the_run_summaries_with_the_api_key():
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Open-Wearables-API-Key"] == "test-key"
        seen.append((request.url.path, request.url.params.get("limit")))
        return httpx.Response(
            200,
            json=[
                _run(
                    "google_health",
                    "failed",
                    "2026-10-24T10:48:35.938927+00:00",
                    "2026-10-24T10:48:35.938927+00:00",
                )
            ],
        )

    states = await _client(handler).sync_states(OW_USER_ID)
    assert seen == [(f"/api/v1/users/{OW_USER_ID}/sync/runs", "50")]
    assert states == {
        "google_health": {"sync_status": "failing", "last_synced_at": None}
    }


def test_summary_uses_the_newest_run_and_keeps_the_last_success():
    rows = [
        _run(
            "garmin",
            "success",
            "2026-10-24T08:00:00+00:00",
            "2026-10-24T08:00:05+00:00",
        ),
        _run("garmin", "failed", "2026-10-24T09:00:00+00:00", "2026-10-24T09:00:02Z"),
        _run(
            "oura",
            "partial",
            "2026-10-24T09:30:00+00:00",
            "2026-10-24T09:30:09+00:00",
        ),
        _run("whoop", "in_progress", "2026-10-24T09:59:00+00:00"),
        _run("polar", "stale", "2026-10-24T07:00:00+00:00"),
    ]
    assert summarize_sync_runs(rows) == {
        "garmin": {
            "sync_status": "failing",
            "last_synced_at": "2026-10-24T08:00:05+00:00",
        },
        "oura": {"sync_status": "ok", "last_synced_at": "2026-10-24T09:30:09+00:00"},
        "whoop": {"sync_status": "syncing", "last_synced_at": None},
        "polar": {"sync_status": "unknown", "last_synced_at": None},
    }


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"detail": "Not Found"},
        [None, 3, {"provider": "garmin"}, {"status": "success"}],
    ],
)
def test_summary_ignores_unexpected_rows(payload):
    assert summarize_sync_runs(payload) == {}


@pytest.mark.asyncio
async def test_disconnect_revokes_and_deletes_the_provider_data():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["X-Open-Wearables-API-Key"] == "test-key"
        return httpx.Response(204)

    assert await _client(handler).disconnect(OW_USER_ID, "google_health") is True
    assert seen == [
        ("DELETE", f"/api/v1/users/{OW_USER_ID}/connections/google_health/data")
    ]


@pytest.mark.asyncio
async def test_disconnect_reports_a_missing_connection():
    # Open Wearables 0.10.0 names the user in its missing-connection reply.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"detail": f"Connection with ID: {OW_USER_ID} not found."}
        )

    assert await _client(handler).disconnect(OW_USER_ID, "oura") is False


@pytest.mark.asyncio
async def test_disconnect_raises_on_a_404_that_does_not_name_the_user():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    with pytest.raises(OpenWearablesError):
        await _client(handler).disconnect(OW_USER_ID, "oura")


@pytest.mark.asyncio
async def test_disconnect_raises_on_a_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    with pytest.raises(OpenWearablesError):
        await _client(handler).disconnect(OW_USER_ID, "oura")

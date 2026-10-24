"""Deleting an Open Wearables user when the Nutree account is deleted."""

import httpx
import pytest

from src.infra.adapters.open_wearables_client import (
    OpenWearablesClient,
    OpenWearablesError,
)

OW_USER_ID = "7d3f0c1e-2a4b-4c5d-8e9f-0a1b2c3d4e5f"


def _client(handler) -> OpenWearablesClient:
    return OpenWearablesClient(
        "https://wearables.example/api/v1",
        "test-key",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_delete_user_sends_delete_with_api_key_header():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["X-Open-Wearables-API-Key"] == "test-key"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"id": OW_USER_ID})

    assert await _client(handler).delete_user(OW_USER_ID) is True
    assert seen == [("DELETE", f"/api/v1/users/{OW_USER_ID}")]


@pytest.mark.asyncio
async def test_delete_user_accepts_an_empty_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    assert await _client(handler).delete_user(OW_USER_ID) is True


@pytest.mark.asyncio
async def test_delete_user_treats_open_wearables_missing_user_as_deleted():
    # Open Wearables 0.10.0 answers a missing user with this exact detail.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"detail": f"User with ID: {OW_USER_ID} not found."}
        )

    assert await _client(handler).delete_user(OW_USER_ID) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"json": {"detail": "Not Found"}},
        {"json": {"detail": "User with ID: someone-else not found."}},
        {"json": ["Not Found"]},
        {"text": "<html>404 Not Found</html>"},
    ],
)
async def test_delete_user_raises_on_a_404_that_does_not_name_the_user(body):
    # A wrong path or a proxy 404 does not show the health data is gone.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, **body)

    with pytest.raises(OpenWearablesError):
        await _client(handler).delete_user(OW_USER_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 500, 503])
async def test_delete_user_raises_on_other_errors(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "nope"})

    with pytest.raises(OpenWearablesError):
        await _client(handler).delete_user(OW_USER_ID)


@pytest.mark.asyncio
async def test_delete_user_raises_when_open_wearables_is_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(OpenWearablesError):
        await _client(handler).delete_user(OW_USER_ID)


@pytest.mark.asyncio
async def test_other_calls_still_raise_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    with pytest.raises(OpenWearablesError):
        await _client(handler).connections(OW_USER_ID)

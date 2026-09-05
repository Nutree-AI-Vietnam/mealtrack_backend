"""Tests for shared connection-pooled HTTP client."""

import pytest

from src.infra.http.client import close_shared_http_client, get_shared_http_client


@pytest.mark.asyncio
async def test_get_shared_http_client_singleton():
    client1 = get_shared_http_client()
    client2 = get_shared_http_client()

    assert client1 is client2
    assert not client1.is_closed

    await close_shared_http_client()
    assert client1.is_closed

    client3 = get_shared_http_client()
    assert client3 is not client1
    assert not client3.is_closed

    await close_shared_http_client()

"""Unit test: RedisClient ConnectionPool must include socket timeouts."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.cache.redis_client import RedisClient


def test_connection_pool_has_socket_timeout():
    """ConnectionPool.from_url must be called with socket_timeout."""
    with patch(
        "src.infra.cache.redis_client.redis.ConnectionPool.from_url"
    ) as mock_from_url:
        mock_from_url.return_value = MagicMock()
        RedisClient(redis_url="redis://localhost:6379")
        call_kwargs = mock_from_url.call_args[1]
        assert "socket_timeout" in call_kwargs, "socket_timeout must be set"
        assert (
            "socket_connect_timeout" in call_kwargs
        ), "socket_connect_timeout must be set"
        assert call_kwargs["socket_timeout"] > 0
        assert call_kwargs["socket_connect_timeout"] > 0


def test_connection_pool_timeout_is_reasonable():
    """socket_timeout should be <= 10 seconds to avoid blocking too long."""
    with patch(
        "src.infra.cache.redis_client.redis.ConnectionPool.from_url"
    ) as mock_from_url:
        mock_from_url.return_value = MagicMock()
        RedisClient(redis_url="redis://localhost:6379")
        call_kwargs = mock_from_url.call_args[1]
        assert call_kwargs["socket_timeout"] <= 10.0
        assert call_kwargs["socket_connect_timeout"] <= 10.0


def test_connection_pool_timeout_custom_value():
    """Custom timeout values must be forwarded to the pool."""
    with patch(
        "src.infra.cache.redis_client.redis.ConnectionPool.from_url"
    ) as mock_from_url:
        mock_from_url.return_value = MagicMock()
        RedisClient(
            redis_url="redis://localhost:6379",
            socket_timeout=1.5,
            socket_connect_timeout=2.5,
        )
        call_kwargs = mock_from_url.call_args[1]
        assert call_kwargs["socket_timeout"] == 1.5
        assert call_kwargs["socket_connect_timeout"] == 2.5


@pytest.mark.asyncio
async def test_get_reconnects_when_cached_client_belongs_to_old_loop():
    """Loop-bound async Redis clients must be replaced before request use."""
    old_client = MagicMock()
    old_client.aclose = AsyncMock()
    old_pool = MagicMock()
    old_pool.disconnect = AsyncMock()

    new_pool = MagicMock()
    new_client = MagicMock()
    new_client.ping = AsyncMock(return_value=True)
    new_client.get = AsyncMock(return_value="cached-value")

    with (
        patch(
            "src.infra.cache.redis_client.redis.ConnectionPool.from_url",
            return_value=new_pool,
        ),
        patch("src.infra.cache.redis_client.redis.Redis", return_value=new_client),
    ):
        client = RedisClient(redis_url="redis://localhost:6379")
        client.client = old_client
        client.pool = old_pool
        client._loop_id = -1

        result = await client.get("cache-key")

    assert result == "cached-value"
    old_client.aclose.assert_awaited_once()
    old_pool.disconnect.assert_awaited_once()
    new_client.ping.assert_awaited_once()
    new_client.get.assert_awaited_once_with("cache-key")


@pytest.mark.asyncio
async def test_delete_pattern_uses_scan_not_keys():
    """delete_pattern must use scan_iter, never client.keys (blocking)."""
    from unittest.mock import AsyncMock

    from src.infra.cache.redis_client import RedisClient

    client = RedisClient.__new__(RedisClient)

    deleted_keys = []

    async def fake_scan_iter(match):
        for k in ["user:abc:macros:2026-01-01", "user:abc:macros:2026-01-02"]:
            yield k

    mock_client = AsyncMock()
    mock_client.scan_iter = fake_scan_iter

    async def fake_delete(*keys):
        deleted_keys.extend(keys)

    mock_client.delete = AsyncMock(side_effect=fake_delete)
    client.client = mock_client

    count = await client.delete_pattern("user:abc:macros:*")

    assert count == 2
    assert "user:abc:macros:2026-01-01" in deleted_keys
    assert "user:abc:macros:2026-01-02" in deleted_keys
    mock_client.delete.assert_awaited_once()
    assert mock_client.keys.call_count == 0  # must NOT use blocking KEYS

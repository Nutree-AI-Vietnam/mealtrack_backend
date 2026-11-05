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
        assert "socket_connect_timeout" in call_kwargs, (
            "socket_connect_timeout must be set"
        )
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
    scans: list[tuple[str, int]] = []

    async def fake_scan_iter(match, count):
        scans.append((match, count))
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
    # Redis' default COUNT 10 would walk the keyspace in 100x more round trips.
    assert scans == [("user:abc:macros:*", 1_000)]


@pytest.mark.asyncio
async def test_delete_if_revision_not_newer_fences_and_deletes_in_one_script():
    from src.infra.cache.redis_client import RedisClient

    client = RedisClient.__new__(RedisClient)
    mock_client = AsyncMock()
    mock_client.eval = AsyncMock(return_value=1)
    client.client = mock_client
    client._loop_id = None

    async def connected():
        return None

    client.connect = connected

    assert await client.delete_if_revision_not_newer("meal_insight:m1", 42, 600)

    script, numkeys, *args = mock_client.eval.await_args.args
    assert numkeys == 2
    assert args == ["meal_insight:m1", "meal_insight:m1:__revision", "42", "600"]
    assert "DEL" in script and "tonumber(current) > tonumber(ARGV[1])" in script

    mock_client.eval = AsyncMock(return_value=0)
    assert not await client.delete_if_revision_not_newer("meal_insight:m1", 41, 600)


class _Pipeline:
    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self.execute = AsyncMock(return_value=[])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def incr(self, key):
        self.commands.append(("INCR", key))

    def expire(self, key, ttl):
        self.commands.append(("EXPIRE", key, ttl))

    def delete(self, *keys):
        self.commands.append(("DEL", *keys))


@pytest.mark.asyncio
async def test_purge_sends_every_command_in_one_pipeline():
    client = RedisClient(redis_url="redis://localhost:6379")
    client.connect = AsyncMock()
    client.client = MagicMock()
    pipeline = _Pipeline()
    client.client.pipeline = MagicMock(return_value=pipeline)

    assert await client.purge(["a", "b"], {"rev": 60}) is True

    assert pipeline.commands == [
        ("INCR", "rev"),
        ("EXPIRE", "rev", 60),
        ("DEL", "a", "b"),
    ]
    pipeline.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_with_counter_reads_unset_as_zero_and_outage_as_unknown():
    client = RedisClient(redis_url="redis://localhost:6379")
    client.connect = AsyncMock()
    client.client = MagicMock()
    client.client.mget = AsyncMock(side_effect=[[b'{"x": 1}', b"7"], [None, None]])

    assert await client.get_with_counter("k", "rev") == ('{"x": 1}', 7)
    assert await client.get_with_counter("k", "rev") == (None, 0)
    client.client.mget.assert_awaited_with(["k", "rev"])

    client.client = None
    assert await client.get_with_counter("k", "rev") is None

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError

from src.infra.adapters.redis_stream_event_publisher import (
    RedisStreamEventPublisher,
    RedisStreamPublishError,
    stream_key,
)


def _publisher(
    client: AsyncMock, *, max_length: int = 500
) -> RedisStreamEventPublisher:
    return RedisStreamEventPublisher(
        redis_url="redis://localhost:6379/0",
        stream_key="nutreeai_async:events:staging",
        max_length=max_length,
        client_factory=lambda: client,
    )


def test_stream_key_is_scoped_to_environment() -> None:
    assert stream_key("production") == "nutreeai_async:events:production"


def _settings(monkeypatch, **overrides) -> None:
    import src.infra.config.settings as settings_mod

    values = {
        "redis_url": "redis://red-cache:6379",
        "ENVIRONMENT": "staging",
        "INTEGRATION_EVENT_STREAM_MAXLEN": 1234,
        "INTEGRATION_EVENT_REDIS_URL": "",
        **overrides,
    }
    monkeypatch.setattr(settings_mod, "get_settings", lambda: SimpleNamespace(**values))


def test_from_settings_derives_stream_from_environment(monkeypatch) -> None:
    _settings(monkeypatch)

    publisher = RedisStreamEventPublisher.from_settings()

    assert publisher.stream_key == "nutreeai_async:events:staging"
    assert publisher._max_length == 1234
    assert publisher._redis_url == "redis://red-cache:6379"


def test_from_settings_prefers_a_dedicated_event_redis(monkeypatch) -> None:
    _settings(monkeypatch, INTEGRATION_EVENT_REDIS_URL="redis://red-events:6379")

    publisher = RedisStreamEventPublisher.from_settings()

    assert publisher._redis_url == "redis://red-events:6379"


def test_connect_waits_for_a_free_connection_instead_of_dropping() -> None:
    publisher = RedisStreamEventPublisher(
        redis_url="redis://localhost:6379/0",
        stream_key="nutreeai_async:events:staging",
        max_length=10,
    )

    client = publisher._connect()

    pool = client.connection_pool
    assert isinstance(pool, redis.BlockingConnectionPool)
    assert pool.max_connections == 4
    assert client.auto_close_connection_pool is True


@pytest.mark.asyncio
async def test_publish_appends_compact_json_with_approximate_trim() -> None:
    client = AsyncMock()
    payload = {"event_type": "meal.created.v1", "data": {"name": "Phở bò"}}

    await _publisher(client, max_length=500).publish(payload)

    client.xadd.assert_awaited_once()
    args, kwargs = client.xadd.call_args
    assert args[0] == "nutreeai_async:events:staging"
    assert args[1] == {
        "event": '{"event_type":"meal.created.v1","data":{"name":"Phở bò"}}'
    }
    assert json.loads(args[1]["event"]) == payload
    assert kwargs == {"maxlen": 500, "approximate": True}


@pytest.mark.asyncio
async def test_publish_classifies_redis_failure_without_payload() -> None:
    client = AsyncMock()
    client.xadd.side_effect = RedisConnectionError("connection reset")

    with pytest.raises(RedisStreamPublishError) as raised:
        await _publisher(client).publish({"secret": "must-not-be-logged"})

    assert "must-not-be-logged" not in str(raised.value)
    assert "ConnectionError" in str(raised.value)


@pytest.mark.asyncio
async def test_publish_reuses_client_on_the_same_loop() -> None:
    client = AsyncMock()
    built: list[AsyncMock] = []

    def factory() -> AsyncMock:
        built.append(client)
        return client

    publisher = RedisStreamEventPublisher(
        redis_url="redis://localhost:6379/0",
        stream_key="nutreeai_async:events:staging",
        max_length=10,
        client_factory=factory,
    )

    await publisher.publish({"event_id": "1"})
    await publisher.publish({"event_id": "2"})

    assert len(built) == 1
    assert client.xadd.await_count == 2


def test_publish_opens_a_new_client_when_the_loop_changes() -> None:
    first, second = AsyncMock(), AsyncMock()
    publisher = RedisStreamEventPublisher(
        redis_url="redis://localhost:6379/0",
        stream_key="nutreeai_async:events:staging",
        max_length=10,
        client_factory=iter([first, second]).__next__,
    )
    loops = [asyncio.new_event_loop(), asyncio.new_event_loop()]
    try:
        loops[0].run_until_complete(publisher.publish({"event_id": "1"}))
        loops[1].run_until_complete(publisher.publish({"event_id": "2"}))
    finally:
        for loop in loops:
            loop.close()

    first.xadd.assert_awaited_once()
    second.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_aclose_closes_the_client_once() -> None:
    client = AsyncMock()
    publisher = _publisher(client)
    await publisher.publish({"event_id": "1"})

    await publisher.aclose()
    await publisher.aclose()

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_aclose_without_publish_is_noop() -> None:
    client = AsyncMock()

    await _publisher(client).aclose()

    client.aclose.assert_not_called()

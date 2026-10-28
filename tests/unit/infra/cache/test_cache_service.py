"""
Unit tests for CacheService JSON (de)serialization, including the
datetime-with-offset fix that prevented '+HH:MMZ' malformed strings.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.infra.cache.cache_service import CacheService, _json_serializer

# ---------- Serializer ----------


def test_serializer_naive_datetime_appends_z():
    """Naive datetimes are assumed UTC; legacy 'Z' suffix preserved."""
    dt = datetime(2026, 4, 13, 10, 12, 43)
    assert _json_serializer(dt) == "2026-04-13T10:12:43Z"


def test_serializer_tz_aware_datetime_no_double_z():
    """tz-aware datetimes must NOT get a trailing 'Z' (caused +00:00Z bug)."""
    dt = datetime(2026, 4, 13, 10, 12, 43, 247633, tzinfo=UTC)
    out = _json_serializer(dt)
    assert out == "2026-04-13T10:12:43.247633+00:00"
    assert not out.endswith("Z")


# ---------- Round-trip + legacy heal via get_json ----------


@pytest.fixture
def service():
    redis = AsyncMock()
    return CacheService(redis_client=redis, enabled=True)


@pytest.mark.asyncio
async def test_get_json_heals_legacy_offset_z(service):
    """Legacy entries with '+00:00Z' should be sanitized on read."""
    service.redis.get = AsyncMock(
        return_value='{"updated_at": "2026-04-13T10:12:43.247633+00:00Z"}'
    )
    result = await service.get_json("k")
    assert result == {"updated_at": "2026-04-13T10:12:43.247633+00:00"}


@pytest.mark.asyncio
async def test_get_json_heals_negative_offset_z(service):
    """Sanitizer also fixes negative offsets (e.g. '-05:00Z')."""
    service.redis.get = AsyncMock(return_value='{"t": "2026-04-13T10:12:43-05:00Z"}')
    result = await service.get_json("k")
    assert result == {"t": "2026-04-13T10:12:43-05:00"}


@pytest.mark.asyncio
async def test_get_json_passthrough_when_clean(service):
    """Well-formed payloads pass through unchanged."""
    service.redis.get = AsyncMock(
        return_value='{"updated_at": "2026-04-13T10:12:43.247633+00:00"}'
    )
    result = await service.get_json("k")
    assert result == {"updated_at": "2026-04-13T10:12:43.247633+00:00"}


@pytest.mark.asyncio
async def test_get_json_returns_none_on_miss(service):
    service.redis.get = AsyncMock(return_value=None)
    assert await service.get_json("k") is None


@pytest.mark.asyncio
async def test_get_json_returns_none_on_invalid_json(service):
    service.redis.get = AsyncMock(return_value="not-json")
    assert await service.get_json("k") is None


@pytest.mark.asyncio
async def test_set_json_writes_clean_offset(service):
    """Direct cache writes with tz-aware dt produce no '+00:00Z'."""
    service.redis.set = AsyncMock(return_value=True)
    dt = datetime(2026, 4, 13, 10, 12, 43, tzinfo=UTC)
    assert await service.set_json("k", {"updated_at": dt}) is True
    service.redis.set.assert_awaited_once()
    args, _ = service.redis.set.call_args
    payload = args[1]
    assert "+00:00" in payload
    assert "+00:00Z" not in payload


@pytest.mark.asyncio
async def test_set_json_disabled_does_not_write_redis():
    redis = AsyncMock()
    service = CacheService(redis_client=redis, enabled=False)

    assert await service.set_json("k", {"value": 1}) is False
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_deletes_key_and_revision(service):
    service.redis.delete = AsyncMock(return_value=True)

    assert await service.invalidate("user:u:daily") is True
    assert service.redis.delete.await_count == 2
    service.redis.delete.assert_any_await("user:u:daily")
    service.redis.delete.assert_any_await("user:u:daily:__revision")


@pytest.mark.asyncio
async def test_revision_write_keeps_newest_payload(service):
    service.redis.set_if_revision_newer = AsyncMock(return_value=True)

    assert (
        await service.set_json(
            "user:u:metrics",
            {"profile_target_revision": 4},
            revision_field="profile_target_revision",
        )
        is True
    )

    service.redis.set_if_revision_newer.assert_awaited_once()
    args = service.redis.set_if_revision_newer.await_args.args
    assert args[0] == "user:u:metrics"
    assert args[2] == 4


@pytest.mark.asyncio
async def test_invalidate_pattern_deletes_pattern(service):
    service.redis.delete_pattern = AsyncMock(return_value=3)

    assert await service.invalidate_pattern("user:u:activities:*") == 3
    service.redis.delete_pattern.assert_awaited_once_with("user:u:activities:*")


@pytest.mark.asyncio
async def test_purge_deletes_keys_with_fences_and_bumps_in_one_call(service):
    service.redis.purge = AsyncMock(return_value=True)

    await service.purge(["a", "b"], {"user:u1:intake_revision": 60})

    service.redis.purge.assert_awaited_once_with(
        ["a", "b", "a:__revision", "b:__revision"],
        {"user:u1:intake_revision": 60},
    )


@pytest.mark.asyncio
async def test_payload_and_revision_come_back_from_one_read(service):
    service.redis.get_with_counter = AsyncMock(return_value=('{"total": 66}', 3))

    payload, revision = await service.get_json_with_revision(
        "user:u1:nutrition_bulk:a:b", "user:u1:intake_revision"
    )

    assert payload == {"total": 66}
    assert revision == 3
    service.redis.get_with_counter.assert_awaited_once_with(
        "user:u1:nutrition_bulk:a:b", "user:u1:intake_revision"
    )


@pytest.mark.asyncio
async def test_revision_is_unknown_when_redis_is_unavailable(service):
    service.redis.get_with_counter = AsyncMock(return_value=None)

    assert await service.get_json_with_revision("k", "r") == (None, None)


@pytest.mark.asyncio
async def test_revision_is_unknown_when_cache_disabled():
    redis = AsyncMock()
    service = CacheService(redis_client=redis, enabled=False)

    assert await service.get_json_with_revision("k", "r") == (None, None)
    await service.purge(["k"], {"r": 60})
    redis.purge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [True, False, None])
async def test_add_is_one_set_if_absent_call(service, stored):
    """True stored, False already held, None Redis could not answer."""
    service.redis.set_if_not_exists = AsyncMock(return_value=stored)

    assert await service.add("k", "in_flight", 60) is stored
    # JSON, like set(), so get() reads back the same value.
    service.redis.set_if_not_exists.assert_awaited_once_with("k", '"in_flight"', 60)


@pytest.mark.asyncio
async def test_add_when_cache_disabled_cannot_tell():
    redis = AsyncMock()
    service = CacheService(redis_client=redis, enabled=False)

    assert await service.add("k", "v", 60) is None
    redis.set_if_not_exists.assert_not_awaited()

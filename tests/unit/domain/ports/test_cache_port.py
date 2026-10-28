from unittest.mock import MagicMock

import pytest

from src.domain.ports.cache_port import CachePort


def test_cache_port_is_abstract():
    from abc import ABC

    assert issubclass(CachePort, ABC)


def test_cache_port_cannot_be_instantiated():
    with pytest.raises(TypeError):
        CachePort()


def test_cache_service_implements_cache_port():
    from src.infra.cache.cache_service import CacheService

    # Verify that CacheService is a subclass of CachePort
    assert issubclass(CacheService, CachePort)

    # Verify that CacheService can be instantiated and is an instance of CachePort
    mock_redis = MagicMock()
    instance = CacheService(redis_client=mock_redis)
    assert isinstance(instance, CachePort)


class _DictCache(CachePort):
    """A port implementation without its own add: the fallback runs."""

    def __init__(self):
        self.values: dict = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ttl_seconds):
        self.values[key] = value

    async def invalidate(self, key):
        return self.values.pop(key, None) is not None

    async def invalidate_pattern(self, pattern):
        return 0


@pytest.mark.asyncio
async def test_add_fallback_stores_only_into_an_empty_key():
    cache = _DictCache()

    assert await cache.add("k", "first", 60) is True
    assert await cache.add("k", "second", 60) is False
    assert cache.values == {"k": "first"}

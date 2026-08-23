from unittest.mock import AsyncMock

import pytest

from src.infra.cache.provider_budget import MemoryProviderBudget, RedisProviderBudget


@pytest.mark.asyncio
async def test_redis_provider_budget_accepts_calls_inside_limit():
    redis = AsyncMock()
    redis.incr_with_expiry.return_value = 3

    allowed = await RedisProviderBudget(redis).acquire("fatsecret", 10)

    assert allowed is True
    redis.incr_with_expiry.assert_awaited_once()
    key, ttl = redis.incr_with_expiry.await_args.args
    assert key.startswith("nutrition:provider-budget:v1:fatsecret:")
    assert ttl == 61


@pytest.mark.asyncio
async def test_redis_provider_budget_fails_closed_on_unavailable_or_exhausted_budget():
    redis = AsyncMock()
    budget = RedisProviderBudget(redis)

    redis.incr_with_expiry.return_value = None
    assert await budget.acquire("fatsecret", 10) is False

    redis.incr_with_expiry.return_value = 11
    assert await budget.acquire("fatsecret", 10) is False


@pytest.mark.asyncio
async def test_memory_provider_budget_accepts_calls_inside_limit():
    budget = MemoryProviderBudget(window_seconds=60)

    assert await budget.acquire("fatsecret", 2) is True
    assert await budget.acquire("fatsecret", 2) is True
    assert await budget.acquire("fatsecret", 2) is False

"""Unit tests for MealListCacheService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infra.cache.meal_list_cache_service import MealListCacheService


@pytest.mark.unit
class TestMealListCacheService:
    @pytest.mark.asyncio
    async def test_get_revision_returns_int(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value="5")
        service = MealListCacheService(redis_client=redis)

        rev = await service.get_revision("u-123")
        assert rev == 5
        redis.get.assert_awaited_once_with("user:u-123:meal-lists-revision:v1")

    @pytest.mark.asyncio
    async def test_get_revision_default_zero(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        service = MealListCacheService(redis_client=redis)

        rev = await service.get_revision("u-123")
        assert rev == 0

    @pytest.mark.asyncio
    async def test_increment_revision(self):
        redis = MagicMock()
        redis.incr = AsyncMock(return_value=6)
        service = MealListCacheService(redis_client=redis)

        new_rev = await service.increment_revision("u-123")
        assert new_rev == 6
        redis.incr.assert_awaited_once_with("user:u-123:meal-lists-revision:v1")

    @pytest.mark.asyncio
    async def test_recent_meals_cache_round_trip(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value='{"items": [{"meal_id": "m1"}], "total": 1}')
        redis.set = AsyncMock(return_value=True)
        service = MealListCacheService(redis_client=redis)

        expected_key = service._recent_key("u-1", 3, "America/New_York", "en")
        data = await service.get_recent_meals("u-1", 3, "America/New_York", "en")
        assert data == {"items": [{"meal_id": "m1"}], "total": 1}
        redis.get.assert_awaited_once_with(expected_key)

        await service.set_recent_meals("u-1", 3, "America/New_York", "en", data)
        redis.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_favorite_meals_cache_round_trip(self):
        redis = MagicMock()
        redis.get = AsyncMock(return_value='{"items": [{"meal_id": "m2"}], "total": 1}')
        redis.set = AsyncMock(return_value=True)
        service = MealListCacheService(redis_client=redis)

        expected_key = service._favorite_key("u-1", 3, "vi")
        data = await service.get_favorite_meals("u-1", 3, "vi")
        assert data == {"items": [{"meal_id": "m2"}], "total": 1}
        redis.get.assert_awaited_once_with(expected_key)

        await service.set_favorite_meals("u-1", 3, "vi", data)
        redis.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_client_safe_fallback(self):
        service = MealListCacheService(redis_client=None)

        assert await service.get_revision("u-1") == 0
        assert await service.increment_revision("u-1") == 0
        assert await service.get_recent_meals("u-1", 0, "UTC", "en") is None
        assert await service.get_favorite_meals("u-1", 0, "en") is None

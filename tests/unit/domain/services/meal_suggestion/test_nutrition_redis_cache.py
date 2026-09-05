import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.services.meal_suggestion.nutrition_lookup_service import (
    NutritionLookupService,
)


@pytest.mark.asyncio
async def test_lookup_checks_redis_before_db():
    """Verify Redis is checked before DB lookup."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(
        return_value=json.dumps(
            {
                "protein": 31.0,
                "carbs": 0.0,
                "fat": 3.6,
                "fiber": 0.0,
                "sugar": 0.0,
                "source_tier": "T1_food_reference",
            }
        )
    )

    repo_mock = MagicMock()
    repo_mock.find_by_normalized_name = MagicMock()  # Should NOT be called

    svc = NutritionLookupService(
        food_ref_repo=repo_mock,
        ingredient_nutrition_resolver=MagicMock(),
        generation_service=MagicMock(),
        redis_client=redis_mock,
    )

    result = await svc._lookup_ingredient("chicken breast", 150.0)

    redis_mock.get.assert_called_once()
    repo_mock.find_by_normalized_name.assert_not_called()
    assert result.protein == pytest.approx(46.5, rel=0.01)  # 31 * 1.5


@pytest.mark.asyncio
async def test_lookup_schedules_cache_population_on_miss():
    """Verify successful lookup uses the cache port for population."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)  # Cache miss
    cache_mock = AsyncMock()

    repo_mock = MagicMock()
    repo_mock.find_by_normalized_name = MagicMock(
        return_value={
            "id": 1,
            "protein_100g": 31.0,
            "carbs_100g": 0.0,
            "fat_100g": 3.6,
            "fiber_100g": 0.0,
            "sugar_100g": 0.0,
        }
    )

    svc = NutritionLookupService(
        food_ref_repo=repo_mock,
        ingredient_nutrition_resolver=MagicMock(),
        generation_service=MagicMock(),
        redis_client=redis_mock,
        cache_service=cache_mock,
    )

    await svc._lookup_ingredient("chicken breast", 100.0)

    cache_mock.set.assert_awaited_once()
    call_args = cache_mock.set.call_args
    assert "nutrition:" in call_args.args[0]  # Key contains prefix
    assert call_args.args[2] == 86400  # TTL is 24 hours
    redis_mock.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_uses_cache_port_for_background_population():
    """Application wiring sends cache writes through the async cache port."""
    cache_mock = AsyncMock()
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)

    repo_mock = MagicMock()
    repo_mock.find_by_normalized_name = MagicMock(
        return_value={
            "id": 1,
            "protein_100g": 31.0,
            "carbs_100g": 0.0,
            "fat_100g": 3.6,
            "fiber_100g": 0.0,
            "sugar_100g": 0.0,
        }
    )

    svc = NutritionLookupService(
        food_ref_repo=repo_mock,
        ingredient_nutrition_resolver=MagicMock(),
        generation_service=MagicMock(),
        redis_client=redis_mock,
        cache_service=cache_mock,
    )

    await svc._lookup_ingredient("chicken breast", 100.0)

    cache_mock.set.assert_awaited_once()
    redis_mock.set.assert_not_awaited()

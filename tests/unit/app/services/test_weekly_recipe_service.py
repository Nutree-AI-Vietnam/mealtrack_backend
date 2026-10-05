import json
from decimal import Decimal

import pytest

from src.app.services.weekly_recipe_service import WeeklyRecipeService
from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.nutrition.micros import Micros


def _meal(recipe_id: str, name: str) -> CatalogMeal:
    return CatalogMeal(
        id=recipe_id,
        catalog_key=recipe_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("10"),
        carbs_g=Decimal("20"),
        fat_g=Decimal("3"),
        fiber_g=Decimal("2"),
        meal_types=("dinner",),
    )


class _Catalog:
    def __init__(self):
        self.get_meals_calls = []
        self.fence_locks = 0

    async def list_active_meals(self, **kwargs):
        assert kwargs["meal_type"] == "dinner"
        return (
            _meal("dinner", "Grilled tofu"),
            _meal("dessert", "Chocolate tofu pudding"),
            _meal("remedy", "Herbal cough remedy"),
        )

    async def lock_catalog_publication(self, *, shared=True):
        self.fence_locks += 1
        return "test-v1"

    async def capture_catalog_publication_version(self):
        return "test-v1"

    async def get_meals(self, ids):
        self.get_meals_calls.append(list(ids))
        return [_meal(mid, f"Meal {mid}") for mid in ids]


class _UnitOfWork:
    def __init__(self, catalog=None):
        self.catalog_recipes = catalog or _Catalog()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _MockRedisClient:
    def __init__(self, store=None):
        self.store = dict(store or {})
        self.mset_calls = []

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def mset_with_ttl(self, mapping, ttl):
        self.mset_calls.append((dict(mapping), ttl))
        self.store.update(mapping)
        return True


@pytest.mark.asyncio
async def test_summaries_hits_redis_and_fetches_only_missing(monkeypatch):
    cached_meal = _meal("r1", "Cached Pho")
    key_r1 = "catalog:summary:v2:test-v1:r1"
    redis = _MockRedisClient({key_r1: json.dumps(cached_meal.to_dict())})

    catalog = _Catalog()
    service = WeeklyRecipeService(lambda: _UnitOfWork(catalog), redis_client=redis)

    results = await service.summaries(["r1", "r2"])

    assert len(results) == 2
    assert results[0].name == "Cached Pho"
    assert results[1].name == "Meal r2"

    # Only r2 should have been queried from database
    assert catalog.get_meals_calls == [["r2"]]

    # r2 should have been saved to Redis
    assert len(redis.mset_calls) == 1
    key_r2 = "catalog:summary:v2:test-v1:r2"
    assert key_r2 in redis.mset_calls[0][0]
    assert redis.mset_calls[0][1] == CacheKeys.TTL_7_DAYS


@pytest.mark.asyncio
async def test_summaries_all_hits_avoids_db(monkeypatch):
    m1 = _meal("r1", "Dish 1")
    m2 = _meal("r2", "Dish 2")
    redis = _MockRedisClient(
        {
            "catalog:summary:v2:test-v1:r1": json.dumps(m1.to_dict()),
            "catalog:summary:v2:test-v1:r2": json.dumps(m2.to_dict()),
        }
    )

    catalog = _Catalog()
    service = WeeklyRecipeService(lambda: _UnitOfWork(catalog), redis_client=redis)

    results = await service.summaries(["r1", "r2"])

    assert len(results) == 2
    assert results[0].name == "Dish 1"
    assert results[1].name == "Dish 2"
    # Zero DB calls!
    assert catalog.get_meals_calls == []


@pytest.mark.asyncio
async def test_summaries_without_redis_falls_back_to_db():
    catalog = _Catalog()
    service = WeeklyRecipeService(lambda: _UnitOfWork(catalog), redis_client=None)

    results = await service.summaries(["r1", "r2"])

    assert len(results) == 2
    assert catalog.get_meals_calls == [["r1", "r2"]]


def test_catalog_meal_serialization_roundtrip():
    original = CatalogMeal(
        id="c1",
        catalog_key="key1",
        content_hash="h" * 64,
        name="Com Tam",
        cuisine="vietnamese",
        description="Broken rice",
        image_url="https://img.example/1.jpg",
        protein_g=Decimal("25.5"),
        carbs_g=Decimal("50.0"),
        fat_g=Decimal("12.0"),
        fiber_g=Decimal("3.5"),
        sugar_g=Decimal("2.0"),
        meal_types=("lunch", "dinner"),
        nutrition_micros=Micros(iron=4.5, calcium=120.0),
    )

    data = original.to_dict()
    restored = CatalogMeal.from_dict(data)

    assert original == restored
    assert restored.calories == original.calories


@pytest.mark.asyncio
async def test_slow_optional_cache_falls_back_within_one_budget_and_skips_write(
    monkeypatch,
):
    import asyncio
    from time import monotonic

    class SlowRedis(_MockRedisClient):
        async def mget(self, keys):
            await asyncio.sleep(1)

    redis = SlowRedis()
    catalog = _Catalog()
    start = monotonic()
    result = await WeeklyRecipeService(
        lambda: _UnitOfWork(catalog), redis_client=redis
    ).summaries(["r1"])
    assert monotonic() - start < 0.3
    assert result[0].id == "r1"
    assert redis.mset_calls == []


@pytest.mark.asyncio
async def test_legacy_unversioned_cache_cannot_override_authoritative_summary():
    redis = _MockRedisClient(
        {
            CacheKeys.catalog_recipe("r1")[0]: json.dumps(
                _meal("r1", "Withdrawn old name").to_dict()
            )
        }
    )
    result = await WeeklyRecipeService(
        lambda: _UnitOfWork(), redis_client=redis
    ).summaries(["r1"])
    assert result[0].name == "Meal r1"

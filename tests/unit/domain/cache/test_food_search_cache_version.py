from src.domain.cache.cache_keys import CacheKeys


def test_food_search_cache_key_is_versioned_for_nutrition_contract():
    key, ttl = CacheKeys.food_search("Rice")

    assert key == "food:search:v2:nutrition_integrity_v1:rice"
    assert ttl == CacheKeys.TTL_7_DAYS


def test_food_search_cache_namespace_includes_generation_after_integrity_transition():
    key, _ = CacheKeys.food_search(
        "Rice",
        policy_version="nutrition_integrity_v1",
        generation=12,
    )

    assert (
        key
        == "food:search:v3:catalog_adopt_v1:nutrition_integrity_v1:generation:12:rice"
    )

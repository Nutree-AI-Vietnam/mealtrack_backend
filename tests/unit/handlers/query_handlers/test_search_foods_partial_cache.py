"""Tests for canonical acquisition and outcome-aware localized search caching."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.handlers.query_handlers.search_foods_query_handler import (
    SearchFoodsQueryHandler,
)
from src.app.queries.food.search_foods_query import SearchFoodsQuery
from src.domain.model.translation_result import TranslationOutcome, TranslationResult
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceSearchProjection,
)
from src.observability import (
    reset_observability_connector_for_test,
    set_observability_connector_for_test,
)


class _Metrics:
    def __init__(self):
        self.calls = []

    def initialize(self):
        return None

    def capture_exception(self, error, *, context=None):
        return None

    def capture_message(self, message, *, level="info", context=None):
        return None

    def log_event(self, level, message, *, attributes=None):
        return None

    def increment_metric(self, name, value=1.0, *, unit=None, attributes=None):
        self.calls.append(("increment", name, value, unit, attributes))

    def gauge_metric(self, name, value, *, unit=None, attributes=None):
        self.calls.append(("gauge", name, value, unit, attributes))

    def distribution_metric(self, name, value, *, unit=None, attributes=None):
        self.calls.append(("distribution", name, value, unit, attributes))

    def set_request_context(self, *, request_id, method, path, user_id=None):
        return None

    def start_span(self, *, operation, description=None, context=None):
        from contextlib import nullcontext

        return nullcontext()

    def flush(self, *, timeout=5):
        return None


def teardown_function():
    reset_observability_connector_for_test()


class _FakeFoodReferenceRepo:
    def __init__(self, adopted: dict | None = None):
        self.adopted = adopted or {"id": 501}
        self.calls: list[tuple] = []

    async def adopt_provider_food(
        self,
        namespace,
        food_id,
        english_name,
        per_100g,
        servings,
        locale,
        locale_name,
    ):
        self.calls.append(
            (namespace, food_id, english_name, per_100g, servings, locale, locale_name)
        )
        return self.adopted


class _FakeUow:
    def __init__(self, repo: _FakeFoodReferenceRepo):
        self.food_references = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class _FakeUowFactory:
    def __init__(self, repo: _FakeFoodReferenceRepo):
        self.repo = repo
        self.created = 0

    def __call__(self):
        self.created += 1
        return _FakeUow(self.repo)


def _local_rice() -> FoodReferenceSearchProjection:
    return FoodReferenceSearchProjection(
        id=7,
        name="Rice",
        name_normalized="rice",
        brand=None,
        source="catalog_seed",
        is_verified=True,
        protein_100g=2.7,
        carbs_100g=28.0,
        fat_100g=0.3,
        fiber_100g=0.4,
        sugar_100g=0.1,
        serving_options=[{"unit": "g", "gram_weight": 1.0, "description": "1 g"}],
    )


def _make_handler(localized_results=None, fallback_results=None, local_results=None):
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)  # always cache miss
    cache.cache_search = AsyncMock()

    fat_secret = MagicMock()
    # First call: localized; second call: English fallback
    fat_secret.search_foods = AsyncMock(
        side_effect=[
            localized_results or [],
            fallback_results or [],
        ]
    )

    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda x: x

    local_search = AsyncMock(return_value=local_results or [])

    return (
        SearchFoodsQueryHandler(
            cache_service=cache,
            mapping_service=mapping,
            fat_secret_service=fat_secret,
            translation_service=None,
            local_search=local_search,
        ),
        fat_secret,
        cache,
        local_search,
    )


class _NeutralTranslator:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def translate_texts(self, texts, source_language, target_language):
        self.calls.append((list(texts), source_language, target_language))
        return self.outcomes.pop(0)


@pytest.mark.asyncio
async def test_partial_localized_result_skips_fallback():
    """When localized search returns results (even partial), fallback must NOT run."""
    partial_results = [{"description": "Phở", "source": "fatsecret"}]
    handler, fat_secret, _, _ = _make_handler(localized_results=partial_results)

    query = SearchFoodsQuery(query="pho", language="vi", limit=10)
    await handler.handle(query)

    assert fat_secret.search_foods.call_count == 1, (
        f"Expected 1 fatsecret call (localized only), got {fat_secret.search_foods.call_count}"
    )


@pytest.mark.asyncio
async def test_already_localized_result_is_cached():
    """Vietnamese-only names have no leftovers and are safe to cache."""
    localized_results = [{"description": "Phở", "source": "fatsecret"}]
    handler, _, cache, _ = _make_handler(localized_results=localized_results)

    query = SearchFoodsQuery(query="pho", language="vi", limit=10)
    await handler.handle(query)

    cache.cache_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_english_search_translates_query_and_only_full_results_are_cached():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock(
        return_value=[{"description": "Rice Bowl", "source": "fatsecret"}]
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: item
    local_search = AsyncMock(return_value=[])
    translator = _NeutralTranslator(
        [
            TranslationResult(("rice",), TranslationOutcome.TRANSLATED, "vi", "en"),
            TranslationResult(("Cơm tô",), TranslationOutcome.TRANSLATED, "en", "vi"),
        ]
    )
    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        translation_service=translator,
        local_search=local_search,
    )

    result = await handler.handle(SearchFoodsQuery(query="cơm", language="vi"))

    assert result["results"][0]["description"] == "Cơm tô"
    assert translator.calls == [
        (["cơm"], "vi", "en"),
        (["Rice Bowl"], "en", "vi"),
    ]
    fat_secret.search_foods.assert_awaited_once_with("rice", max_results=20)
    cache.cache_search.assert_awaited_once()
    assert cache.cache_search.call_args.args[0].startswith("food-search:v2:vi:")


@pytest.mark.asyncio
async def test_non_english_partial_forward_result_is_presented_without_cache_write():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock(
        return_value=[
            {"description": "Rice Bowl", "source": "fatsecret"},
            {"description": "Chicken", "source": "fatsecret"},
        ]
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: item
    translator = _NeutralTranslator(
        [
            TranslationResult(("rice",), TranslationOutcome.TRANSLATED, "vi", "en"),
            TranslationResult(
                ("Cơm tô", "Chicken"), TranslationOutcome.PARTIAL, "en", "vi"
            ),
        ]
    )
    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        translation_service=translator,
    )

    result = await handler.handle(SearchFoodsQuery(query="cơm", language="vi"))

    assert [item["description"] for item in result["results"]] == [
        "Cơm tô",
        "Gà",
    ]
    cache.cache_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_localized_result_uses_canonical_provider_once():
    """Canonical acquisition does not make a second locale-provider request."""
    fallback_results = [{"description": "Noodle soup", "source": "fatsecret"}]
    handler, fat_secret, _, _ = _make_handler(
        localized_results=[], fallback_results=fallback_results
    )

    query = SearchFoodsQuery(query="pho", language="vi", limit=10)
    await handler.handle(query)

    assert fat_secret.search_foods.call_count == 1
    fat_secret.search_foods.assert_awaited_once_with("pho", max_results=10)


@pytest.mark.asyncio
async def test_english_search_calls_fatsecret_when_local_is_empty():
    """Manual food search should use FatSecret on cache miss after local misses."""
    handler, fat_secret, cache, local_search = _make_handler(
        localized_results=[{"description": "Chicken breast", "source": "fatsecret"}]
    )

    query = SearchFoodsQuery(query="chicken", language="en", limit=7)
    result = await handler.handle(query)

    cache.get_cached_search.assert_awaited_once_with(
        SearchFoodsQueryHandler._cache_key("chicken", "en")
    )
    local_search.assert_awaited_once_with("chicken", "US", 7)
    fat_secret.search_foods.assert_awaited_once_with("chicken", max_results=7)
    assert result["results"][0]["source"] == "fatsecret"


@pytest.mark.asyncio
async def test_search_returns_local_results_without_provider_call():
    handler, fat_secret, _, local_search = _make_handler(local_results=[_local_rice()])

    result = await handler.handle(
        SearchFoodsQuery(query="rice", language="en", limit=1)
    )

    local_search.assert_awaited_once_with("rice", "US", 1)
    fat_secret.search_foods.assert_not_awaited()
    assert result["results"][0]["source"] == "food_reference"
    assert result["results"][0]["food_reference_id"] == 7


@pytest.mark.asyncio
async def test_search_metrics_are_bounded_and_do_not_include_query_text():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    handler, _, _, _ = _make_handler(local_results=[_local_rice()])

    await handler.handle(
        SearchFoodsQuery(query="secret rice query", language="en", limit=1)
    )

    assert (
        "distribution",
        "food_search.operation.latency_ms",
    ) == metrics.calls[0][:2]
    assert (
        "increment",
        "food_search.requests",
    ) == metrics.calls[1][:2]
    attributes = metrics.calls[1][4]
    assert attributes == {
        "operation": "search",
        "source": "local",
        "language": "en",
        "status": "success",
    }
    assert "secret rice query" not in str(metrics.calls)


@pytest.mark.asyncio
async def test_provider_outage_returns_partial_local_results():
    handler, fat_secret, _, _ = _make_handler(local_results=[_local_rice()])
    fat_secret.search_foods = AsyncMock(side_effect=Exception("provider unavailable"))

    result = await handler.handle(
        SearchFoodsQuery(query="rice", language="en", limit=5)
    )

    assert result["results"] == [
        handler.mapping_service.map_search_item(
            handler._local_projection_to_raw(_local_rice())
        )
    ]


@pytest.mark.asyncio
async def test_cache_outage_degrades_to_local_and_provider_results():
    handler, fat_secret, cache, local_search = _make_handler(
        localized_results=[{"description": "Rice cakes", "source": "fatsecret"}],
        local_results=[_local_rice()],
    )
    cache.get_cached_search = AsyncMock(side_effect=Exception("redis unavailable"))
    cache.cache_search = AsyncMock(side_effect=Exception("redis unavailable"))

    result = await handler.handle(
        SearchFoodsQuery(query="rice", language="en", limit=5)
    )

    cache.get_cached_search.assert_awaited_once_with(
        SearchFoodsQueryHandler._cache_key("rice", "en")
    )
    local_search.assert_awaited_once_with("rice", "US", 5)
    fat_secret.search_foods.assert_awaited_once_with("rice", max_results=4)
    assert [item["source"] for item in result["results"]] == [
        "food_reference",
        "fatsecret",
    ]


@pytest.mark.asyncio
async def test_provider_duplicate_does_not_replace_verified_local_result():
    handler, fat_secret, _, _ = _make_handler(
        localized_results=[
            {
                "description": "Rice",
                "name_normalized": "rice",
                "source": "fatsecret",
            }
        ],
        local_results=[_local_rice()],
    )

    result = await handler.handle(
        SearchFoodsQuery(query="rice", language="en", limit=5)
    )

    fat_secret.search_foods.assert_awaited_once_with("rice", max_results=4)
    assert len(result["results"]) == 1
    assert result["results"][0]["source"] == "food_reference"


@pytest.mark.asyncio
async def test_cached_search_respects_requested_limit():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(
        return_value=[
            {"description": "Rice", "source": "fatsecret"},
            {"description": "Rice noodles", "source": "fatsecret"},
        ]
    )
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock()
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda x: x
    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
    )

    result = await handler.handle(
        SearchFoodsQuery(query="rice", language="en", limit=1, autocomplete=True)
    )

    assert len(result["results"]) == 1
    fat_secret.search_foods.assert_not_awaited()


@pytest.mark.asyncio
async def test_detailed_fatsecret_hit_is_adopted_and_mapped_with_food_reference_id():
    """A fully-resolved FatSecret search hit adopts once and links its id."""
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock(
        return_value=[
            {
                "description": "Grilled Chicken Breast",
                "source": "fatsecret",
                "source_namespace": "fatsecret",
                "source_food_id": "12345",
                "food_id": "12345",
                "protein_100g": 31.0,
                "carbs_100g": 0.0,
                "fat_100g": 3.6,
                "fiber_100g": 0.0,
                "sugar_100g": 0.0,
                "serving_options": [
                    {"unit": "g", "gram_weight": 100.0, "description": "100 g"}
                ],
            }
        ]
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: dict(item)
    repo = _FakeFoodReferenceRepo(adopted={"id": 777})
    uow_factory = _FakeUowFactory(repo)

    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        local_search=AsyncMock(return_value=[]),
        uow_factory=uow_factory,
    )

    result = await handler.handle(
        SearchFoodsQuery(query="chicken", language="en", limit=5)
    )

    assert len(repo.calls) == 1
    namespace, food_id, english_name, per_100g, servings, locale, locale_name = (
        repo.calls[0]
    )
    assert namespace == "fatsecret"
    assert food_id == "12345"
    assert english_name == "Grilled Chicken Breast"
    assert per_100g["protein_100g"] == 31.0
    assert locale == "en"
    assert locale_name == "Grilled Chicken Breast"
    assert result["results"][0]["food_reference_id"] == 777


@pytest.mark.asyncio
async def test_autocomplete_search_never_adopts():
    """Autocomplete/thin candidate search must never write to the catalog."""
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock(
        return_value=[
            {
                "description": "Grilled Chicken Breast",
                "source": "fatsecret",
                "source_namespace": "fatsecret",
                "source_food_id": "12345",
                "food_id": "12345",
                "protein_100g": 31.0,
                "carbs_100g": 0.0,
                "fat_100g": 3.6,
            }
        ]
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: dict(item)
    repo = _FakeFoodReferenceRepo()
    uow_factory = _FakeUowFactory(repo)

    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        local_search=AsyncMock(return_value=[]),
        uow_factory=uow_factory,
    )

    result = await handler.handle(
        SearchFoodsQuery(query="chicken", language="en", limit=5, autocomplete=True)
    )

    assert repo.calls == []
    assert uow_factory.created == 0
    assert "food_reference_id" not in result["results"][0]


@pytest.mark.asyncio
async def test_thin_provider_hit_without_macros_is_not_adopted():
    """A candidate with no durable macros must not trigger an adopt write."""
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock(
        return_value=[
            {
                "description": "Mystery Dish",
                "source": "fatsecret",
                "source_namespace": "fatsecret",
                "source_food_id": "999",
                "food_id": "999",
                "protein_100g": None,
                "carbs_100g": None,
                "fat_100g": None,
            }
        ]
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: dict(item)
    repo = _FakeFoodReferenceRepo()
    uow_factory = _FakeUowFactory(repo)

    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        local_search=AsyncMock(return_value=[]),
        uow_factory=uow_factory,
    )

    await handler.handle(SearchFoodsQuery(query="mystery", language="en", limit=5))

    assert repo.calls == []


@pytest.mark.asyncio
async def test_localized_search_adopts_before_translation_overwrites_name():
    """Non-English search adopts using the canonical English name, not the
    translated display name."""
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock(
        return_value=[
            {
                "description": "Beef Noodle Soup",
                "source": "fatsecret",
                "source_namespace": "fatsecret",
                "source_food_id": "555",
                "food_id": "555",
                "protein_100g": 6.0,
                "carbs_100g": 10.0,
                "fat_100g": 2.0,
            }
        ]
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: dict(item)
    repo = _FakeFoodReferenceRepo(adopted={"id": 888})
    uow_factory = _FakeUowFactory(repo)
    translator = _NeutralTranslator(
        [
            TranslationResult(("bo",), TranslationOutcome.TRANSLATED, "vi", "en"),
            TranslationResult(
                ("Phở bò",), TranslationOutcome.TRANSLATED, "en", "vi"
            ),
        ]
    )

    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        translation_service=translator,
        local_search=AsyncMock(return_value=[]),
        uow_factory=uow_factory,
    )

    result = await handler.handle(
        SearchFoodsQuery(query="bo", language="vi", limit=5)
    )

    assert len(repo.calls) == 1
    english_name = repo.calls[0][2]
    assert english_name == "Beef Noodle Soup"
    assert result["results"][0]["food_reference_id"] == 888
    assert result["results"][0]["description"] == "Phở bò"


@pytest.mark.asyncio
async def test_local_only_vietnamese_result_is_localized():
    handler, fat_secret, _, _ = _make_handler(local_results=[_local_rice()])

    result = await handler.handle(
        SearchFoodsQuery(query="cơm", language="vi", limit=1)
    )

    fat_secret.search_foods.assert_not_awaited()
    assert result["results"][0]["description"] == "Cơm"


@pytest.mark.asyncio
async def test_cached_english_leftovers_are_ignored_for_vietnamese():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(
        return_value=[
            {"description": "Chicken", "name": "Chicken", "source": "fatsecret"}
        ]
    )
    cache.cache_search = AsyncMock()
    fat_secret = MagicMock()
    fat_secret.search_foods = AsyncMock(
        return_value=[{"description": "Chicken", "source": "fatsecret"}]
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: item
    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        local_search=AsyncMock(return_value=[]),
    )

    result = await handler.handle(SearchFoodsQuery(query="Gà", language="vi"))

    fat_secret.search_foods.assert_awaited_once()
    assert result["results"][0]["description"] == "Gà"
    cache.cache_search.assert_awaited_once()

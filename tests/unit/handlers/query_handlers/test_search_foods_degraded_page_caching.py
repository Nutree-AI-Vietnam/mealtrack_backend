"""Pages built while a search source failed are served but never cached."""

import copy
from contextlib import nullcontext
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
        return nullcontext()

    def flush(self, *, timeout=5):
        return None


def teardown_function():
    reset_observability_connector_for_test()


class _LocalSearch:
    """Catalog lookup that raises for the queries listed in ``failing``."""

    def __init__(self, results_by_query=None, *, failing=()):
        self.results_by_query = results_by_query or {}
        self.failing = set(failing)
        self.calls: list[tuple] = []

    async def __call__(self, query, region, limit):
        self.calls.append((query, region, limit))
        if query in self.failing:
            raise RuntimeError("catalog unavailable")
        return list(self.results_by_query.get(query, []))


class _Translator:
    """Maps texts; translating into English can raise or end another way."""

    def __init__(self, mapping=None, *, to_english=TranslationOutcome.TRANSLATED):
        self.mapping = mapping or {}
        self.to_english = to_english
        self.calls: list[tuple] = []

    async def translate_texts(self, texts, source_language, target_language):
        self.calls.append((list(texts), source_language, target_language))
        if target_language == "en" and isinstance(self.to_english, Exception):
            raise self.to_english
        if target_language == "en" and self.to_english is not (
            TranslationOutcome.TRANSLATED
        ):
            return TranslationResult(
                tuple(texts), self.to_english, source_language, target_language
            )
        return TranslationResult(
            tuple(self.mapping.get(text, text) for text in texts),
            TranslationOutcome.TRANSLATED,
            source_language,
            target_language,
        )


class _Provider:
    def __init__(self, results=None):
        self.results = results or []
        self.calls: list[tuple] = []

    async def search_food_candidates(self, query, max_results):
        self.calls.append((query, max_results))
        return copy.deepcopy(self.results)


def _projection(food_id, name, *, name_vi=None):
    return FoodReferenceSearchProjection(
        id=food_id,
        name=name,
        name_normalized=name.lower(),
        brand=None,
        source="catalog_seed",
        is_verified=True,
        protein_100g=10.0,
        carbs_100g=20.0,
        fat_100g=5.0,
        allowed_units=[{"unit": "g", "gram_weight": 1.0, "description": "1 g"}],
        name_vi=name_vi,
    )


def _provider_hit(food_id, description):
    return {
        "source": "fatsecret",
        "source_namespace": "fatsecret",
        "source_food_id": food_id,
        "food_id": food_id,
        "description": description,
        "name": description,
    }


def _cache():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    return cache


def _handler(*, local, cache, provider=None, translator=None):
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: dict(item)
    return SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=mapping,
        fat_secret_service=provider,
        translation_service=translator,
        local_search=local,
    )


async def _search(handler, text, language="en"):
    result = await handler.handle(
        SearchFoodsQuery(query=text, language=language, limit=5)
    )
    await handler.drain()
    return result


def _status(metrics):
    return next(
        call[4]["status"] for call in metrics.calls if call[1] == "food_search.requests"
    )


def _descriptions(result):
    return [item["description"] for item in result["results"]]


@pytest.mark.asyncio
async def test_english_page_after_failed_catalog_lookup_is_served_but_not_cached():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    provider = _Provider(
        [_provider_hit("9", "Brown rice"), _provider_hit("10", "Rice")]
    )
    cache = _cache()
    handler = _handler(
        local=_LocalSearch(failing={"rice"}), provider=provider, cache=cache
    )

    result = await _search(handler, "rice")

    assert provider.calls == [("rice", 5)]
    assert _descriptions(result) == ["Brown rice", "Rice"]
    assert result["partial"] is True
    assert _status(metrics) == "degraded"
    cache.cache_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_catalog_lookup_without_provider_is_reported_as_degraded():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    handler = _handler(local=_LocalSearch(failing={"rice"}), cache=_cache())

    result = await _search(handler, "rice")

    assert result["results"] == []
    assert result["partial"] is True
    assert _status(metrics) == "degraded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "to_english",
    [RuntimeError("translator timed out"), TranslationOutcome.UNAVAILABLE],
    ids=["raises", "unavailable"],
)
async def test_translator_outage_page_is_served_partial_and_not_cached(to_english):
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    local = _LocalSearch({"phở": [_projection(11, "Beef pho", name_vi="Phở bò")]})
    cache = _cache()
    handler = _handler(
        local=local,
        provider=_Provider(),
        translator=_Translator(to_english=to_english),
        cache=cache,
    )

    result = await _search(handler, "phở", "vi")

    assert local.calls == [("phở", "VN", 5)]
    assert _descriptions(result) == ["Phở bò"]
    assert result["partial"] is True
    assert _status(metrics) == "degraded"
    cache.cache_search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("found_by", "failing"),
    [("beef pho", "phở bò"), ("phở bò", "beef pho")],
    ids=["typed-text-lookup-fails", "english-lookup-fails"],
)
async def test_page_missing_one_catalog_lookup_is_served_partial_and_not_cached(
    found_by, failing
):
    local = _LocalSearch(
        {found_by: [_projection(12, "Beef pho", name_vi="Phở bò")]},
        failing={failing},
    )
    cache = _cache()
    handler = _handler(
        local=local,
        provider=_Provider(),
        translator=_Translator({"phở bò": "beef pho"}),
        cache=cache,
    )

    result = await _search(handler, "phở bò", "vi")

    assert local.calls == [("phở bò", "VN", 5), ("beef pho", "US", 5)]
    assert _descriptions(result) == ["Phở bò"]
    assert result["partial"] is True
    cache.cache_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_the_translator_keeps_unchanged_is_still_cached():
    # A word shared with English comes back unchanged on every request, so
    # the page built from the typed text is already the whole answer.
    local = _LocalSearch({"pizza": [_projection(41, "Pizza", name_vi="Bánh pizza")]})
    cache = _cache()
    handler = _handler(
        local=local,
        provider=_Provider(),
        translator=_Translator(to_english=TranslationOutcome.PARTIAL),
        cache=cache,
    )

    result = await _search(handler, "pizza", "vi")

    assert local.calls == [("pizza", "VN", 5)]
    assert _descriptions(result) == ["Bánh pizza"]
    assert "partial" not in result
    cache.cache_search.assert_awaited_once()

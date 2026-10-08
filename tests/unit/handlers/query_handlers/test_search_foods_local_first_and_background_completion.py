"""Local-first food search with provider work that outlives the request."""

import asyncio
import copy
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.handlers.query_handlers import search_foods_query_handler
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


class _FakeFoodReferenceRepo:
    def __init__(self, gate: asyncio.Event | None = None):
        self.gate = gate
        self.calls: list[tuple] = []
        self.upserted: list[tuple] = []
        self.applied: list[dict] = []

    async def adopt_provider_food(
        self, namespace, food_id, english_name, per_100g, servings, locale, locale_name
    ):
        if self.gate is not None:
            await self.gate.wait()
        self.calls.append(
            (
                namespace,
                food_id,
                english_name,
                per_100g,
                copy.deepcopy(servings),
                locale,
                locale_name,
            )
        )
        return {"id": 501}

    async def get_by_source_identities(self, identities):
        return []

    async def get_serving_phrase_translations(self, phrases, language):
        return {}

    async def upsert_serving_phrase_translations(self, labels, language):
        self.upserted.append((dict(labels), language))

    async def apply_serving_name_vi_many(self, by_reference):
        self.applied.append(copy.deepcopy(by_reference))


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


class _Translator:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping
        self.calls: list[tuple] = []

    async def translate_texts(self, texts, source_language, target_language):
        self.calls.append((list(texts), source_language, target_language))
        return TranslationResult(
            tuple(self.mapping.get(text, text) for text in texts),
            TranslationOutcome.TRANSLATED,
            source_language,
            target_language,
        )


class _Provider:
    def __init__(self, results=None, *, error=None, gate=None):
        self.results = results or []
        self.error = error
        self.gate = gate
        self.calls: list[tuple] = []

    async def search_food_candidates(self, query, max_results):
        self.calls.append((query, max_results))
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.results)


class _LocalSearch:
    def __init__(self, results_by_query: dict[str, list] | None = None):
        self.results_by_query = results_by_query or {}
        self.calls: list[tuple] = []

    async def __call__(self, query, region, limit):
        self.calls.append((query, region, limit))
        return list(self.results_by_query.get(query, []))


def _projection(
    food_id, name, *, name_vi=None, source_namespace=None, source_food_id=None
):
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
        source_namespace=source_namespace,
        source_food_id=source_food_id,
        name_vi=name_vi,
    )


def _provider_hit(food_id, description, **extra):
    return {
        "source": "fatsecret",
        "source_namespace": "fatsecret",
        "source_food_id": food_id,
        "food_id": food_id,
        "description": description,
        "name": description,
        **extra,
    }


def _query(text, language="en", *, limit=5, autocomplete=False):
    return SearchFoodsQuery(
        query=text, language=language, limit=limit, autocomplete=autocomplete
    )


def _cache():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=None)
    cache.cache_search = AsyncMock()
    return cache


def _handler(*, local, provider=None, translator=None, uow_factory=None, cache=None):
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: dict(item)
    return SearchFoodsQueryHandler(
        cache_service=cache if cache is not None else _cache(),
        mapping_service=mapping,
        fat_secret_service=provider,
        translation_service=translator,
        local_search=local,
        uow_factory=uow_factory,
    )


def _request_attributes(metrics):
    return next(call[4] for call in metrics.calls if call[1] == "food_search.requests")


def _descriptions(result):
    return [item["description"] for item in result["results"]]


@pytest.mark.asyncio
async def test_vietnamese_local_hits_answer_without_translator_or_provider():
    names = ["Phở bò tái", "Phở gà", "Phở bò chín", "Phở xào", "Phở cuốn"]
    rows = [
        _projection(index, f"Pho {index}", name_vi=name)
        for index, name in enumerate(names, start=1)
    ]
    local = _LocalSearch({"phở": rows})
    translator = _Translator({})
    provider = _Provider([_provider_hit("1", "Beef pho")])
    cache = _cache()
    handler = _handler(
        local=local, provider=provider, translator=translator, cache=cache
    )

    result = await handler.handle(_query("phở", "vi"))
    await handler.drain()

    assert _descriptions(result) == names
    assert "partial" not in result
    assert translator.calls == []
    assert provider.calls == []
    assert local.calls == [("phở", "VN", 5)]
    cache.cache_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_english_search_ranks_strong_local_then_provider_then_weak_local():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    local = _LocalSearch(
        {
            "raw": [
                _projection(1, "Raw honey"),
                _projection(
                    2, "Strawberry", source_namespace="fatsecret", source_food_id="123"
                ),
                _projection(3, "Drawn butter"),
            ]
        }
    )
    provider = _Provider(
        [_provider_hit("123", "Strawberries"), _provider_hit("456", "Raw milk")]
    )
    cache = _cache()
    handler = _handler(local=local, provider=provider, cache=cache)

    result = await handler.handle(_query("raw"))
    await handler.drain()

    # One strong local row leaves four slots for the provider page.
    assert provider.calls == [("raw", 4)]
    # The provider's strawberry is the catalog's row 2, so the catalog row
    # takes the provider's place; the substring-only "Drawn butter" trails.
    assert _descriptions(result) == [
        "Raw honey",
        "Strawberry",
        "Raw milk",
        "Drawn butter",
    ]
    assert result["results"][1]["food_reference_id"] == 2
    assert "partial" not in result
    attributes = _request_attributes(metrics)
    assert attributes["source"] == "mixed"
    assert attributes["status"] == "success"
    cache.cache_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_english_search_retries_local_catalog_with_english_query():
    local = _LocalSearch({"chicken": [_projection(21, "Chicken breast")]})
    translator = _Translator({"poulet": "chicken"})
    provider = _Provider([_provider_hit("31", "Chicken thigh")])
    handler = _handler(local=local, provider=provider, translator=translator)

    result = await handler.handle(_query("poulet", "fr"))
    await handler.drain()

    assert local.calls == [("poulet", "US", 5), ("chicken", "US", 5)]
    assert translator.calls[0] == (["poulet"], "fr", "en")
    assert provider.calls == [("chicken", 4)]
    assert len(result["results"]) == 2
    assert result["results"][0]["food_reference_id"] == 21


@pytest.mark.asyncio
async def test_late_provider_page_returns_local_rows_and_still_reaches_cache(
    monkeypatch,
):
    monkeypatch.setattr(
        search_foods_query_handler, "completion_budget_seconds", lambda **_: 0.01
    )
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    gate = asyncio.Event()
    provider = _Provider([_provider_hit("9", "Brown rice")], gate=gate)
    cache = _cache()
    handler = _handler(
        local=_LocalSearch({"rice": [_projection(7, "Rice")]}),
        provider=provider,
        cache=cache,
    )

    result = await handler.handle(_query("rice"))

    assert _descriptions(result) == ["Rice"]
    assert result["partial"] is True
    attributes = _request_attributes(metrics)
    assert attributes["status"] == "timeout"
    assert attributes["source"] == "local"
    cache.cache_search.assert_not_awaited()

    gate.set()
    await handler.drain()

    cache.cache_search.assert_awaited_once()
    cached = cache.cache_search.await_args.args[1]
    assert [item["description"] for item in cached] == ["Rice", "Brown rice"]


@pytest.mark.asyncio
async def test_identical_concurrent_searches_share_one_provider_call():
    gate = asyncio.Event()
    provider = _Provider([_provider_hit("5", "Rice noodles")], gate=gate)
    handler = _handler(local=_LocalSearch(), provider=provider)

    async def release_provider():
        for _ in range(100):
            if provider.calls:
                break
            await asyncio.sleep(0)
        gate.set()

    first, second, _ = await asyncio.gather(
        handler.handle(_query("rice")),
        handler.handle(_query("rice")),
        release_provider(),
    )
    await handler.drain()

    assert provider.calls == [("rice", 5)]
    assert first == second
    assert _descriptions(first) == ["Rice noodles"]


@pytest.mark.asyncio
async def test_repeat_search_joins_work_still_writing_the_cache():
    cache_gate = asyncio.Event()

    async def slow_cache_write(*args, **kwargs):
        await cache_gate.wait()

    cache = _cache()
    cache.cache_search = AsyncMock(side_effect=slow_cache_write)
    provider = _Provider([_provider_hit("5", "Rice noodles")])
    handler = _handler(local=_LocalSearch(), provider=provider, cache=cache)

    first = await handler.handle(_query("rice"))
    second = await handler.handle(_query("rice"))

    assert provider.calls == [("rice", 5)]
    assert first == second

    cache_gate.set()
    await handler.drain()

    cache.cache_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_english_provider_failure_returns_local_rows_as_degraded():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    cache = _cache()
    handler = _handler(
        local=_LocalSearch({"rice": [_projection(7, "Rice")]}),
        provider=_Provider(error=RuntimeError("provider unavailable")),
        cache=cache,
    )

    result = await handler.handle(_query("rice"))
    await handler.drain()

    assert _descriptions(result) == ["Rice"]
    assert result["partial"] is True
    attributes = _request_attributes(metrics)
    assert attributes["status"] == "degraded"
    assert attributes["source"] == "local"
    cache.cache_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_vietnamese_provider_failure_keeps_local_rows_uncached():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    local = _LocalSearch({"gà": [_projection(11, "Boiled chicken", name_vi="Gà luộc")]})
    cache = _cache()
    handler = _handler(
        local=local,
        provider=_Provider(error=RuntimeError("provider unavailable")),
        translator=_Translator({"gà": "chicken"}),
        cache=cache,
    )

    result = await handler.handle(_query("gà", "vi"))
    await handler.drain()

    assert local.calls == [("gà", "VN", 5), ("chicken", "US", 5)]
    assert _descriptions(result) == ["Gà luộc"]
    assert result["partial"] is True
    assert _request_attributes(metrics)["status"] == "degraded"
    cache.cache_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_keys_separate_mode_and_limit_but_ignore_case_and_spacing():
    cache = _cache()
    handler = _handler(local=_LocalSearch(), cache=cache)

    for event in (
        _query("rice"),
        _query("rice", autocomplete=True),
        _query("rice", limit=10),
        _query("  RICE "),
    ):
        await handler.handle(event)

    keys = [call.args[0] for call in cache.get_cached_search.await_args_list]
    assert len(set(keys[:3])) == 3
    assert keys[3] == keys[0]
    assert all(len(key) <= 64 for key in keys)


@pytest.mark.asyncio
async def test_metrics_report_request_then_stages_without_query_text():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    handler = _handler(
        local=_LocalSearch({"secret soup": [_projection(1, "Clear broth")]})
    )

    await handler.handle(_query("secret soup"))

    assert metrics.calls[0][:2] == ("distribution", "food_search.operation.latency_ms")
    assert metrics.calls[1][:2] == ("increment", "food_search.requests")
    stages = metrics.calls[2:]
    assert {call[1] for call in stages} == {"food_search.stage.latency_ms"}
    assert {call[3] for call in stages} == {"millisecond"}
    assert [call[4]["stage"] for call in stages] == ["cache_read", "local"]
    assert all(set(call[4]) == {"stage", "language", "mode"} for call in stages)
    assert "secret soup" not in repr(metrics.calls)


@pytest.mark.asyncio
async def test_adoption_receives_provider_units_from_before_localization():
    repo = _FakeFoodReferenceRepo()
    serving = [{"unit": "serving", "gram_weight": 120.0, "description": "1 serving"}]
    provider = _Provider(
        [
            _provider_hit(
                "77",
                "Chicken breast",
                metric_serving_amount=120.0,
                protein_100g=31.0,
                carbs_100g=0.0,
                fat_100g=3.6,
                allowed_units=serving,
            )
        ]
    )
    translator = _Translator({"ức gà": "chicken breast", "Chicken breast": "Ức gà"})
    handler = _handler(
        local=_LocalSearch(),
        provider=provider,
        translator=translator,
        uow_factory=_FakeUowFactory(repo),
    )

    result = await handler.handle(_query("ức gà", "vi"))
    await handler.drain()

    item = result["results"][0]
    assert item["description"] == "Ức gà"
    assert item["allowed_units"][0]["display_description"] == "Khẩu phần"
    # The catalog stores the provider's own unit text; the Vietnamese label
    # lives in the phrase table and the serving's name_vi.
    assert repo.calls[0] == (
        "fatsecret",
        "77",
        "Chicken breast",
        {
            "protein_100g": 31.0,
            "carbs_100g": 0.0,
            "fat_100g": 3.6,
            "fiber_100g": 0,
            "sugar_100g": 0,
        },
        serving,
        "vi",
        "Ức gà",
    )
    assert repo.upserted == [({"serving": "Khẩu phần"}, "vi")]
    assert repo.applied == [{501: {"serving": "Khẩu phần"}}]


@pytest.mark.asyncio
async def test_response_does_not_wait_for_catalog_adoption():
    gate = asyncio.Event()
    repo = _FakeFoodReferenceRepo(gate=gate)
    provider = _Provider(
        [
            _provider_hit(
                "88",
                "Greek yogurt",
                metric_serving_amount=100.0,
                protein_100g=10.0,
                carbs_100g=4.0,
                fat_100g=0.4,
            )
        ]
    )
    cache = _cache()
    handler = _handler(
        local=_LocalSearch(),
        provider=provider,
        uow_factory=_FakeUowFactory(repo),
        cache=cache,
    )

    result = await handler.handle(_query("yogurt"))

    assert _descriptions(result) == ["Greek yogurt"]
    assert "food_reference_id" not in result["results"][0]
    assert repo.calls == []

    gate.set()
    await handler.drain()

    assert len(repo.calls) == 1
    assert cache.cache_search.await_args.args[1][0]["food_reference_id"] == 501


@pytest.mark.asyncio
async def test_vietnamese_english_named_rows_localize_in_the_background():
    english = [
        "Salmon fillet",
        "Salmon roe",
        "Smoked salmon",
        "Salmon steak",
        "Salmon sashimi",
    ]
    vietnamese = [
        "Phi lê cá hồi",
        "Trứng cá hồi",
        "Cá hồi hun khói",
        "Bít tết cá hồi",
        "Cá hồi sống",
    ]
    rows = [_projection(index, name) for index, name in enumerate(english, start=1)]
    translator = _Translator(dict(zip(english, vietnamese, strict=True)))
    provider = _Provider([])
    cache = _cache()
    handler = _handler(
        local=_LocalSearch({"cá hồi": rows}),
        provider=provider,
        translator=translator,
        cache=cache,
    )

    result = await handler.handle(_query("cá hồi", "vi"))

    # A full local page answers now; names are translated after the response.
    assert result["partial"] is True
    assert _descriptions(result) == english
    assert provider.calls == []
    assert translator.calls == []

    await handler.drain()

    assert translator.calls
    cache.cache_search.assert_awaited_once()
    cached = cache.cache_search.await_args.args[1]
    assert [item["description"] for item in cached] == vietnamese

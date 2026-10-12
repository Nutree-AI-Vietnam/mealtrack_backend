"""Cache hits must preserve a complete saveable parse response."""

import asyncio
import copy
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.schemas.meal_schemas import ParsedFoodItemDto, ParseMealTextResponseDto
from src.app.services.parse_text_response_cache import (
    CACHE_VERSION,
    parse_text_cache_key,
    read_parse_text_cache,
    write_parse_text_cache,
)
from src.domain.services.nutrition_integrity_policy import (
    NUTRITION_INTEGRITY_POLICY_VERSION,
)


def _response():
    allowed_units = [
        {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
        {"unit": "kg", "gram_weight": 1000.0, "description": "1 kg"},
    ]
    density = {
        "protein_per_100g": 10,
        "carbs_per_100g": 65,
        "fat_per_100g": 22,
        "fiber_per_100g": 10,
        "sugar_per_100g": 20,
        "calories_per_100g": 478,
    }
    item = ParsedFoodItemDto(
        name="Ngũ cốc granola",
        canonical_name="Granola",
        quantity=50,
        unit="g",
        protein=5,
        carbs=32.5,
        fat=11,
        fiber=5,
        sugar=10,
        data_source="custom",
        origin="custom",
        nutrition_basis="100g",
        nutrition_contract_version=NUTRITION_INTEGRITY_POLICY_VERSION,
        allowed_units=allowed_units,
        **density,
        source_snapshot={
            "basis": "100g",
            "canonical_name": "Granola",
            "origin": "custom",
            "source_namespace": None,
            "source_food_id": None,
            "allowed_units": allowed_units,
            **density,
            "extra_nutrients": {
                "vitamin_a": 0,
                "vitamin_c": None,
                "vitamin_e": 2,
                "calcium": 50,
                "iron": 3,
                "magnesium": 150,
                "potassium": 350,
                "sodium": 5,
                "saturated_fat": 4,
                "added_sugar": 10,
            },
        },
    )
    return ParseMealTextResponseDto(
        items=[item], total_protein=5, total_carbs=32.5, total_fat=11, emoji="🥣"
    )


@pytest.mark.asyncio
async def test_cache_round_trip_retains_every_dto_field_and_all_ten_micro_states():
    response = _response()
    cache = SimpleNamespace(get=AsyncMock(), set=AsyncMock())

    await write_parse_text_cache(cache, "key", response, ttl_seconds=60)
    cached_data = cache.set.await_args.args[1]
    cache.get.return_value = copy.deepcopy(cached_data)
    hit = await read_parse_text_cache(cache, "key")

    assert hit is not None
    assert asdict(hit) == asdict(response)
    extras = hit.items[0].source_snapshot["extra_nutrients"]
    assert len(extras) == 10
    assert extras["vitamin_a"] == 0
    assert extras["vitamin_c"] is None
    assert extras["added_sugar"] == 10
    cache.set.assert_awaited_once_with("key", cached_data, ttl_seconds=60)


@pytest.mark.parametrize(
    ("origin", "data_source", "identity"),
    [
        (
            "local",
            "local",
            {
                "food_reference_id": 12,
                "source_namespace": "food_reference",
                "source_food_id": "12",
            },
        ),
        (
            "usda",
            "usda",
            {"fdc_id": 123, "source_namespace": "usda", "source_food_id": "123"},
        ),
        (
            "provider",
            "fatsecret",
            {
                "food_id": "fs-123",
                "source_namespace": "fatsecret",
                "source_food_id": "fs-123",
            },
        ),
    ],
)
def test_cache_preserves_distinct_food_provenance(origin, data_source, identity):
    from src.app.services.parse_text_response_cache import response_from_cache

    response = _response()
    item = response.items[0]
    item.origin = origin
    item.data_source = data_source
    for key, value in identity.items():
        setattr(item, key, value)
        item.source_snapshot[key] = value
    item.source_snapshot["origin"] = origin

    hit = response_from_cache({"version": CACHE_VERSION, "response": asdict(response)})

    assert hit.items[0].origin == origin
    assert hit.items[0].food_reference_id == identity.get("food_reference_id")
    assert hit.items[0].fdc_id == identity.get("fdc_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, "invalid", {}, {"version": CACHE_VERSION - 1}])
async def test_missing_malformed_and_stale_cache_entries_are_misses(data):
    cache = SimpleNamespace(get=AsyncMock(return_value=data))

    assert await read_parse_text_cache(cache, "key") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("items",), []),
        (("total_protein",), 6),
        (("total_fat",), float("nan")),
        (("items", 0, "quantity"), 0),
        (("items", 0, "fat"), 51),
        (("items", 0, "fiber"), 33),
        (("items", 0, "protein_per_100g"), 11),
        (("items", 0, "calories_per_100g"), 999),
        (("items", 0, "source_snapshot"), None),
        (("items", 0, "source_snapshot", "protein_per_100g"), 11),
        (("items", 0, "source_snapshot", "extra_nutrients", "iron"), -1),
        (("items", 0, "source_snapshot", "extra_nutrients", "iron"), float("inf")),
        (("items", 0, "source_snapshot", "extra_nutrients", "saturated_fat"), 23),
        (("items", 0, "source_snapshot", "extra_nutrients", "added_sugar"), 21),
        (("items", 0, "allowed_units", 0, "gram_weight"), -1),
        (("items", 0, "origin"), "provider"),
        (("unmatched_terms",), ["lost food"]),
    ],
    ids=[
        "empty-success",
        "inconsistent-total",
        "nonfinite-total",
        "zero-amount",
        "impossible-mass",
        "fiber-subset",
        "portion-density-disagrees",
        "derived-energy-disagrees",
        "missing-save-snapshot",
        "snapshot-disagrees",
        "negative-micro",
        "nonfinite-micro",
        "satfat-subset",
        "added-sugar-subset",
        "invalid-serving-weight",
        "provider-without-identity",
        "partial-success",
    ],
)
async def test_corrupt_saveable_cache_payload_is_a_miss(path, value):
    body = asdict(_response())
    target = body
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    cache = SimpleNamespace(
        get=AsyncMock(return_value={"version": CACHE_VERSION, "response": body})
    )

    assert await read_parse_text_cache(cache, "key") is None


@pytest.mark.asyncio
async def test_invalid_response_is_not_published_to_cache():
    response = _response()
    response.total_protein = 999
    cache = SimpleNamespace(set=AsyncMock())

    await write_parse_text_cache(cache, "key", response, ttl_seconds=60)

    cache.set.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "set"])
async def test_optional_cache_timeout_cancels_operation(monkeypatch, operation):
    cancelled = asyncio.Event()

    async def waiting_cache(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        "src.app.services.parse_text_response_cache.CACHE_TIMEOUT_SECONDS", 0.01
    )
    cache = SimpleNamespace(get=AsyncMock(), set=AsyncMock())
    setattr(cache, operation, AsyncMock(side_effect=waiting_cache))
    if operation == "get":
        assert await read_parse_text_cache(cache, "key") is None
    else:
        await write_parse_text_cache(cache, "key", _response(), ttl_seconds=60)

    assert cancelled.is_set()
    assert getattr(cache, operation).await_count == 1


def test_structured_micronutrients_round_trip_and_reject_invalid_amount():
    from src.app.services.parse_text_response_cache import response_from_cache

    body = asdict(_response())
    body["items"][0]["source_snapshot"]["extra_nutrients"]["iron"] = {
        "amount": 3,
        "unit": "mg",
    }
    body["items"][0]["source_snapshot"]["extra_nutrients"]["saturated_fat"] = {
        "amount": 4000,
        "unit": "mg",
    }
    hit = response_from_cache({"version": CACHE_VERSION, "response": body})
    assert hit.items[0].source_snapshot["extra_nutrients"]["iron"] == {
        "amount": 3,
        "unit": "mg",
    }
    from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros

    portion_micros = extra_nutrients_to_micros(
        hit.items[0].source_snapshot["extra_nutrients"], factor=0.5
    )
    assert portion_micros.saturated_fat == 2

    body["items"][0]["source_snapshot"]["extra_nutrients"]["iron"]["amount"] = -1
    with pytest.raises(ValueError, match="micronutrient"):
        response_from_cache({"version": CACHE_VERSION, "response": body})


def test_key_normalizes_text_and_hashes_sensitive_input_and_context():
    assert parse_text_cache_key("  10g  Granola ", "en", "model:guest") == (
        parse_text_cache_key("10g granola", "en", "model:guest")
    )
    key = parse_text_cache_key("10g granola", "en", "model:guest")
    assert "granola" not in key
    assert "model" not in key
    assert "guest" not in key
    assert key != parse_text_cache_key("10g granola", "vi", "model:guest")

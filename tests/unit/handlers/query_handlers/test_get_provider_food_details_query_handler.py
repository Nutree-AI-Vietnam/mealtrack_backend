"""Tests for GetProviderFoodDetailsQueryHandler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.handlers.query_handlers.get_provider_food_details_query_handler import (
    GetProviderFoodDetailsQueryHandler,
)
from src.app.queries.food.get_provider_food_details_query import (
    GetProviderFoodDetailsQuery,
)


class _FakeFoodReferenceRepo:
    def __init__(self):
        self.calls = []

    async def adopt_provider_food(self, *args):
        self.calls.append(args)
        return {"id": 42}


class _FakeUow:
    def __init__(self, repo):
        self.food_references = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeUowFactory:
    def __init__(self, repo):
        self.repo = repo

    def __call__(self):
        return _FakeUow(self.repo)


@pytest.mark.asyncio
async def test_provider_details_fetches_and_maps_one_food():
    fat_secret = MagicMock()
    fat_secret.get_food_details = AsyncMock(
        return_value={
            "description": "Chicken Breast",
            "source": "fatsecret",
            "source_namespace": "fatsecret",
            "source_food_id": "123",
            "food_id": "123",
            "protein_100g": 31.0,
            "carbs_100g": 0.0,
            "fat_100g": 3.6,
            "metric_serving_amount": 100.0,
            "allowed_units": [
                {"unit": "g", "gram_weight": 1.0, "description": "1 g"},
            ],
        }
    )
    mapping = MagicMock()
    mapping.map_search_item.side_effect = lambda item: {
        **item,
        "name": item.get("description"),
        "origin": "provider",
    }
    repo = _FakeFoodReferenceRepo()

    handler = GetProviderFoodDetailsQueryHandler(
        mapping_service=mapping,
        fat_secret_service=fat_secret,
        uow_factory=_FakeUowFactory(repo),
    )

    result = await handler.handle(
        GetProviderFoodDetailsQuery(
            source_namespace="fatsecret",
            source_food_id="123",
            language="en",
        )
    )

    fat_secret.get_food_details.assert_awaited_once()
    assert result["name"] == "Chicken Breast"
    assert result["food_reference_id"] == 42
    assert len(repo.calls) == 1


@pytest.mark.asyncio
async def test_provider_details_rejects_unknown_namespace():
    handler = GetProviderFoodDetailsQueryHandler(
        mapping_service=MagicMock(),
        fat_secret_service=MagicMock(),
    )
    with pytest.raises(ValueError):
        await handler.handle(
            GetProviderFoodDetailsQuery(
                source_namespace="usda",
                source_food_id="1",
            )
        )


@pytest.mark.asyncio
async def test_provider_details_missing_food_raises_lookup():
    fat_secret = MagicMock()
    fat_secret.get_food_details = AsyncMock(return_value=None)
    handler = GetProviderFoodDetailsQueryHandler(
        mapping_service=MagicMock(),
        fat_secret_service=fat_secret,
    )
    with pytest.raises(LookupError):
        await handler.handle(
            GetProviderFoodDetailsQuery(
                source_namespace="fatsecret",
                source_food_id="missing",
            )
        )

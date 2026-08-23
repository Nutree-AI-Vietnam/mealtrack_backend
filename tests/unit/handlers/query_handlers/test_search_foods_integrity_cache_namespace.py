from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.handlers.query_handlers.search_foods_query_handler import (
    SearchFoodsQueryHandler,
)
from src.app.queries.food.search_foods_query import SearchFoodsQuery
from src.domain.services.food_mapping_service import FoodMappingService


@pytest.mark.asyncio
async def test_search_reads_integrity_control_before_cache_and_uses_namespace():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=[])
    context = AsyncMock(
        return_value={"policy_version": "nutrition_integrity_v1", "generation": 8}
    )
    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=FoodMappingService(),
        integrity_context=context,
    )

    result = await handler.handle(SearchFoodsQuery(query="rice", limit=5))

    assert result["results"] == []
    context.assert_awaited_once()
    cache.get_cached_search.assert_awaited_once_with(
        "en:rice",
        policy_version="nutrition_integrity_v1",
        generation=8,
    )


@pytest.mark.asyncio
async def test_search_does_not_read_cache_when_integrity_control_is_unavailable():
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=[{"description": "stale"}])
    context = AsyncMock(side_effect=RuntimeError("control unavailable"))
    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=FoodMappingService(),
        integrity_context=context,
    )

    result = await handler.handle(SearchFoodsQuery(query="rice", limit=5))

    assert result["results"] == []
    cache.get_cached_search.assert_not_awaited()

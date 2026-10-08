"""Pasted text is searched by its start instead of being rejected."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.handlers.query_handlers.search_foods_query_handler import (
    SearchFoodsQueryHandler,
)
from src.app.queries.food.search_foods_query import SearchFoodsQuery
from src.domain.services.food_mapping_service import FoodMappingService
from src.domain.utils.food_search_text import MAX_FOOD_SEARCH_QUERY_LENGTH


@pytest.mark.asyncio
async def test_long_pasted_text_is_cached_searched_and_echoed_by_its_start():
    pasted = "chicken rice with egg " * 40
    start = pasted[:MAX_FOOD_SEARCH_QUERY_LENGTH]
    cache = MagicMock()
    cache.get_cached_search = AsyncMock(return_value=[])
    local_search = AsyncMock(return_value=[])
    handler = SearchFoodsQueryHandler(
        cache_service=cache,
        mapping_service=FoodMappingService(),
        local_search=local_search,
        integrity_context=AsyncMock(
            return_value={"policy_version": "nutrition_integrity_v1", "generation": 8}
        ),
    )

    result = await handler.handle(SearchFoodsQuery(query=pasted, limit=5))

    cache.get_cached_search.assert_awaited_once_with(
        SearchFoodsQueryHandler._cache_key(start, "en", mode="search", limit=5),
        policy_version="nutrition_integrity_v1",
        generation=8,
    )
    assert local_search.await_args.args[0] == start
    assert result == {"results": [], "query": start, "total": 0}

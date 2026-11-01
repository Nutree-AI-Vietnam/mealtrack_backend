import json
import logging
import traceback
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.infra.adapters.food_data_service import FoodDataService


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.is_success = True

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_get_branded_food_by_gtin_returns_exact_normalized_match():
    client = AsyncMock()
    client.get.return_value = _Response(
        {
            "foods": [
                {"fdcId": 1, "gtinUpc": "999999999999"},
                {"fdcId": 2, "gtinUpc": "036000291452"},
            ]
        }
    )
    service = FoodDataService(api_key="key", client=client)

    result = await service.get_branded_food_by_gtin(["00036000291452"])

    assert result is not None
    assert result["fdcId"] == 2


@pytest.mark.asyncio
async def test_get_branded_food_by_gtin_missing_key_returns_none():
    service = FoodDataService(api_key="", client=AsyncMock())

    assert await service.get_branded_food_by_gtin(["00036000291452"]) is None


SYNTHETIC_API_KEY = "synthetic-fdc-key-not-real"


def _usda_response(status_code: int, path: str) -> httpx.Response:
    request = httpx.Request(
        "GET",
        f"https://api.nal.usda.gov/fdc/v1{path}",
        params={"api_key": SYNTHETIC_API_KEY},
    )
    return httpx.Response(status_code, request=request)


def _service_for(response: httpx.Response) -> FoodDataService:
    client = AsyncMock()
    client.get.return_value = response
    return FoodDataService(api_key=SYNTHETIC_API_KEY, client=client)


class _Cache:
    async def get_cached_food(self, fdc_id: int):
        return None

    async def cache_food(self, fdc_id: int, raw: dict) -> None:
        raise AssertionError("a missing food must not be cached")


def _rendered(exc: BaseException, caplog: pytest.LogCaptureFixture) -> str:
    chain = "".join(traceback.format_exception(exc))
    return "\n".join((str(exc), repr(exc), chain, caplog.text))


@pytest.mark.asyncio
async def test_usda_404_maps_to_client_404_without_api_key(caplog):
    from src.api.exception_handlers import _meal_track_exception_handler
    from src.api.exceptions import ResourceNotFoundException
    from src.app.handlers.query_handlers.get_food_details_query_handler import (
        GetFoodDetailsQueryHandler,
    )
    from src.app.queries.food.get_food_details_query import GetFoodDetailsQuery
    from src.domain.services.food_mapping_service import FoodMappingService
    from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus

    service = _service_for(_usda_response(404, "/food/12345"))
    handler = GetFoodDetailsQueryHandler(
        food_data_service=service,
        cache_service=_Cache(),
        mapping_service=FoodMappingService(),
    )
    bus = PyMediatorEventBus()
    bus.register_handler(GetFoodDetailsQuery, handler)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ResourceNotFoundException) as raised:
            await bus.send(GetFoodDetailsQuery(fdc_id=12345))

    response = await _meal_track_exception_handler(MagicMock(), raised.value)
    body = json.loads(response.body)
    assert response.status_code == 404
    assert body["detail"]["error_code"] == "FOOD_NOT_FOUND"
    assert body["detail"]["message"] == "Food not found"
    assert SYNTHETIC_API_KEY not in response.body.decode()
    rendered = _rendered(raised.value, caplog)
    assert SYNTHETIC_API_KEY not in rendered
    assert f"api_key={SYNTHETIC_API_KEY}" not in rendered
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_usda_server_error_log_omits_api_key(caplog):
    from src.app.handlers.query_handlers.get_food_details_query_handler import (
        GetFoodDetailsQueryHandler,
    )
    from src.app.queries.food.get_food_details_query import GetFoodDetailsQuery
    from src.domain.services.food_mapping_service import FoodMappingService
    from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus

    service = _service_for(_usda_response(500, "/food/99"))
    handler = GetFoodDetailsQueryHandler(
        food_data_service=service,
        cache_service=_Cache(),
        mapping_service=FoodMappingService(),
    )
    bus = PyMediatorEventBus()
    bus.register_handler(GetFoodDetailsQuery, handler)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(httpx.HTTPStatusError) as raised:
            await bus.send(GetFoodDetailsQuery(fdc_id=99))

    rendered = _rendered(raised.value, caplog)
    assert SYNTHETIC_API_KEY not in rendered
    assert "api_key=" not in str(raised.value)
    assert "api_key=" not in caplog.text
    assert any(
        record.levelno >= logging.ERROR and "Error handling" in record.message
        for record in caplog.records
    )

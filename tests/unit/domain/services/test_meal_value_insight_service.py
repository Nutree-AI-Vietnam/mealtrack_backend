from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src.api.routes.v1.meals import get_meal_value_insights
from src.domain.services.meal_value_insight_service import MealValueInsightService


class FakeCache:
    def __init__(self, values=None):
        self.values = values or {}

    async def get(self, key):
        return self.values.get(key)


class FakeEventBus:
    def __init__(self, meal):
        self.meal = meal

    async def send(self, query):
        return self.meal


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"accept-language", b"en")],
            "state": {"language": "en"},
        }
    )


def _meal():
    return SimpleNamespace(
        meal_id="00000000-0000-4000-8000-000000000001",
    )


def _payload():
    return {
        "meal_bullets": [
            {
                "text": "Supports fullness",
                "category": "benefit",
                "highlights": ["fullness"],
            }
        ],
        "ingredient_insights": [],
    }


@pytest.mark.asyncio
async def test_reader_uses_worker_owned_meal_key():
    meal = _meal()
    service = MealValueInsightService()
    cache = FakeCache({service.cache_key_for_meal(meal.meal_id): _payload()})

    result = await service.get_cached_for_meal(
        meal_id=meal.meal_id,
        cache_service=cache,
    )

    assert result is not None
    assert result.meal_bullets[0].text == "Supports fullness"


@pytest.mark.asyncio
async def test_reader_rejects_malformed_worker_payload():
    meal = _meal()
    service = MealValueInsightService()
    cache = FakeCache(
        {service.cache_key_for_meal(meal.meal_id): {"meal_bullets": [{"text": "bad"}]}}
    )

    result = await service.get_cached_for_meal(
        meal_id=meal.meal_id,
        cache_service=cache,
    )

    assert result is None


@pytest.mark.asyncio
async def test_status_endpoint_reports_fresh_worker_cache():
    meal = _meal()
    service = MealValueInsightService()
    cache = FakeCache({service.cache_key_for_meal(meal.meal_id): _payload()})

    response = await get_meal_value_insights(
        request=_request(),
        meal_id=meal.meal_id,
        user_id="00000000-0000-4000-8000-000000000002",
        event_bus=FakeEventBus(meal),
        cache_service=cache,
    )

    assert response.status == "fresh"
    assert response.version == service.cache_key_for_meal(meal.meal_id)
    assert response.value_insights is not None


@pytest.mark.asyncio
async def test_status_endpoint_reports_generating_on_cache_miss():
    meal = _meal()
    response = await get_meal_value_insights(
        request=_request(),
        meal_id=meal.meal_id,
        user_id="00000000-0000-4000-8000-000000000002",
        event_bus=FakeEventBus(meal),
        cache_service=FakeCache(),
    )

    assert response.status == "generating"
    assert response.value_insights is None

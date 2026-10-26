from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from src.app.services.meal_insight_recovery import (
    INSIGHT_RECOVERY_COOLDOWN_SECONDS,
    MealInsightRecovery,
)
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition

NOW = datetime(2026, 10, 26, 14, 0, tzinfo=UTC)


class _Cache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> Any | None:
        return self.values.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.values[key] = value
        self.ttls[key] = ttl_seconds


class _Publisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.payloads: list[dict[str, Any]] = []
        self._error = error

    async def publish(self, payload: dict[str, Any]) -> None:
        if self._error is not None:
            raise self._error
        self.payloads.append(payload)


def _meal(*, logged_ago: timedelta, status: MealStatus = MealStatus.READY) -> Meal:
    logged_at = NOW - logged_ago
    return Meal(
        meal_id=str(uuid4()),
        user_id=str(uuid4()),
        status=status,
        created_at=logged_at,
        ready_at=logged_at,
        image=None,
        dish_name="Fried rice",
        source="prompt",
        nutrition=Nutrition(
            macros=Macros(protein=69.1, carbs=121.8, fat=38.2),
            food_items=[
                FoodItem(
                    id="item-1",
                    name="Fried rice",
                    quantity=400,
                    unit="g",
                    macros=Macros(protein=69.1, carbs=121.8, fat=38.2),
                )
            ],
        ),
    )


def _recovery(publisher: _Publisher) -> MealInsightRecovery:
    return MealInsightRecovery(publisher, "production", clock=lambda: NOW)


@pytest.mark.asyncio
async def test_overdue_meal_republishes_its_event_with_the_insight_snapshot():
    publisher = _Publisher()
    cache = _Cache()
    meal = _meal(logged_ago=timedelta(hours=3))

    requested = await _recovery(publisher).request_if_missing(
        meal=meal, cache_service=cache, language="vi"
    )

    assert requested is True
    [payload] = publisher.payloads
    assert payload["event_type"] == "meal.updated.v1"
    assert payload["environment"] == "production"
    assert payload["data"]["meal_id"] == meal.meal_id
    assert payload["data"]["language"] == "vi"
    assert "insight" in payload["data"]
    guard = f"meal_insight_recovery:{meal.meal_id}"
    assert cache.ttls[guard] == INSIGHT_RECOVERY_COOLDOWN_SECONDS


@pytest.mark.asyncio
async def test_recovery_runs_once_per_cooldown():
    publisher = _Publisher()
    cache = _Cache()
    meal = _meal(logged_ago=timedelta(days=2))
    recovery = _recovery(publisher)

    first = await recovery.request_if_missing(
        meal=meal, cache_service=cache, language="en"
    )
    second = await recovery.request_if_missing(
        meal=meal, cache_service=cache, language="en"
    )

    assert (first, second) == (True, False)
    assert len(publisher.payloads) == 1


@pytest.mark.parametrize(
    ("logged_ago", "status"),
    [
        (timedelta(seconds=30), MealStatus.READY),
        (timedelta(days=8), MealStatus.READY),
        (timedelta(hours=1), MealStatus.INACTIVE),
    ],
)
@pytest.mark.asyncio
async def test_no_recovery_while_the_worker_may_still_answer_or_after_the_ttl(
    logged_ago, status
):
    publisher = _Publisher()
    cache = _Cache()

    requested = await _recovery(publisher).request_if_missing(
        meal=_meal(logged_ago=logged_ago, status=status),
        cache_service=cache,
        language="en",
    )

    assert requested is False
    assert publisher.payloads == []
    assert cache.values == {}


@pytest.mark.asyncio
async def test_drinks_never_request_insights():
    publisher = _Publisher()
    drink = _meal(logged_ago=timedelta(hours=1))
    drink.meal_type = "hydration"

    requested = await _recovery(publisher).request_if_missing(
        meal=drink, cache_service=_Cache(), language="en"
    )

    assert requested is False
    assert publisher.payloads == []


@pytest.mark.asyncio
async def test_a_failed_publish_never_fails_the_status_read():
    publisher = _Publisher(error=RuntimeError("queue down"))

    requested = await _recovery(publisher).request_if_missing(
        meal=_meal(logged_ago=timedelta(hours=1)),
        cache_service=_Cache(),
        language="en",
    )

    assert requested is False

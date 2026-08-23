"""
Tests for timing instrumentation in CreateManualMealCommandHandler.
Verifies that handler awaits cache invalidation (not fire-and-forget) and
that timing log messages are emitted.
"""

import asyncio
import logging
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.meal.create_manual_meal_command import (
    CreateManualMealCommand,
    CustomNutrition,
    ManualMealItem,
)
from src.app.handlers.command_handlers.create_manual_meal_command_handler import (
    CreateManualMealCommandHandler,
)
from src.app.services.cache_invalidation_service import CacheInvalidationService

# ---------------------------------------------------------------------------
# Minimal fakes (no mocking framework dependency for simple objects)
# ---------------------------------------------------------------------------


class _FakeMeals:
    def __init__(self, fake_meal):
        self._meal = fake_meal

    async def save(self, meal):
        return self._meal


class _FakeUsers:
    async def find_by_id(self, user_id):
        return None


class _FakeUow:
    def __init__(self, fake_meal):
        self.meals = _FakeMeals(fake_meal)
        self.users = _FakeUsers()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _PreparedV2Meals:
    async def save(self, meal):
        return meal


class _PreparedV2WriteOperations:
    def __init__(self):
        self.completed = None

    async def complete(self, reservation, *, target_meal_id, response):
        self.completed = (reservation, target_meal_id, response)


class _PreparedV2Uow:
    def __init__(self):
        self.meals = _PreparedV2Meals()
        self.meal_write_operations = _PreparedV2WriteOperations()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_meal():
    m = MagicMock()
    m.meal_id = "test-meal-id"
    m.created_at = None
    return m


_UUID_1 = "550e8400-e29b-41d4-a716-446655440001"
_UUID_2 = "550e8400-e29b-41d4-a716-446655440002"


def _make_command(user_id: str = _UUID_1) -> CreateManualMealCommand:
    return CreateManualMealCommand(
        user_id=user_id,
        items=[
            ManualMealItem(
                fdc_id=None,
                name="Rice",
                quantity=100.0,
                unit="g",
                custom_nutrition=CustomNutrition(
                    calories_per_100g=130.0,
                    protein_per_100g=2.7,
                    carbs_per_100g=28.0,
                    fat_per_100g=0.3,
                ),
            )
        ],
        dish_name="Rice Bowl",
        meal_type="lunch",
        target_date=None,
        source="manual",
        emoji=None,
    )


# ---------------------------------------------------------------------------
# Test A: slow cache delay is visible in elapsed time (handler awaits it)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_timing_logs_show_cache_delay(caplog):
    """Handler must await cache invalidation; a slow cache inflates elapsed time."""
    DELAY_S = 0.05  # 50 ms per operation

    slow_cache = MagicMock()

    async def slow_invalidate(key):
        await asyncio.sleep(DELAY_S)

    async def slow_invalidate_pattern(pattern):
        await asyncio.sleep(DELAY_S)

    slow_cache.invalidate = slow_invalidate
    slow_cache.invalidate_pattern = slow_invalidate_pattern

    cache_svc = CacheInvalidationService(cache=slow_cache)
    fake_meal = _make_meal()
    handler = CreateManualMealCommandHandler(
        uow=_FakeUow(fake_meal),
        cache_invalidation=cache_svc,
    )

    cmd = _make_command()

    t_start = time.perf_counter()
    with caplog.at_level(logging.INFO):
        result = await handler.handle(cmd)
    elapsed = time.perf_counter() - t_start

    # If handler awaits cache, elapsed must be ≥ one slow operation delay
    assert elapsed >= DELAY_S, (
        f"Handler returned in {elapsed * 1000:.0f}ms — expected ≥{DELAY_S * 1000:.0f}ms. "
        "Cache invalidation may not be awaited."
    )

    timing_logs = [
        r.message for r in caplog.records if "manual_save handler timing" in r.message
    ]
    assert timing_logs, (
        "No 'manual_save handler timing' log found — instrumentation missing."
    )

    # Result is the saved meal
    assert result is fake_meal


@pytest.mark.asyncio
async def test_v2_prepared_custom_nutrition_is_saved_without_resolution():
    """A confirmed portion must not be reinterpreted as per-100g nutrition."""
    item = ManualMealItem(
        name="Pork rib",
        quantity=1,
        unit="slice",
        origin="custom",
        nutrition_contract_version="2",
        custom_nutrition=CustomNutrition(
            calories_per_100g=810,
            protein_per_100g=90,
            carbs_per_100g=0,
            fat_per_100g=50,
        ),
    )
    command = CreateManualMealCommand(
        user_id=_UUID_1,
        items=[item],
        dish_name="Rice plate",
        source="prompt",
        nutrition_contract_version=2,
        idempotency_key="write-1",
        request_fingerprint="fingerprint-1",
    )
    resolver = MagicMock()
    resolver.resolve_items = AsyncMock()
    resolver.revalidate_local_items = AsyncMock()
    uow = _PreparedV2Uow()
    handler = CreateManualMealCommandHandler(
        uow=uow,
        uow_factory=object(),
        nutrition_resolver=resolver,
    )
    handler._reserve_v2_write_short = AsyncMock(
        return_value=SimpleNamespace(state="claimed")
    )
    handler._release_v2_write = AsyncMock()

    meal = await handler.handle(command)

    assert meal.nutrition.macros.protein == pytest.approx(27)
    assert meal.nutrition.macros.fat == pytest.approx(15)
    assert meal.nutrition.calories == pytest.approx(243)
    resolver.resolve_items.assert_not_awaited()
    resolver.revalidate_local_items.assert_not_awaited()
    assert uow.meal_write_operations.completed[1] == meal.meal_id
    saved_item = meal.nutrition.food_items[0]
    assert saved_item.source_snapshot["basis"] == "100g"
    assert saved_item.source_snapshot["protein_per_100g"] == 90
    assert saved_item.source_snapshot["origin"] == "custom"


@pytest.mark.asyncio
async def test_v2_prepared_nutrition_uses_confirmed_food_specific_unit():
    item = ManualMealItem(
        name="Pork rib",
        quantity=1,
        unit="slice",
        origin="custom",
        nutrition_contract_version="2",
        allowed_units=[
            {"unit": "g", "gram_weight": 1, "description": "1 g"},
            {"unit": "slice", "gram_weight": 80, "description": "1 slice"},
        ],
        source_snapshot={"basis": "100g"},
        custom_nutrition=CustomNutrition(
            calories_per_100g=810,
            protein_per_100g=90,
            carbs_per_100g=0,
            fat_per_100g=50,
        ),
    )
    command = CreateManualMealCommand(
        user_id=_UUID_1,
        items=[item],
        dish_name="Rice plate",
        nutrition_contract_version=2,
        idempotency_key="write-2",
        request_fingerprint="fingerprint-2",
    )
    resolver = MagicMock()
    resolver.resolve_items = AsyncMock()
    resolver.revalidate_local_items = AsyncMock()
    uow = _PreparedV2Uow()
    handler = CreateManualMealCommandHandler(
        uow=uow,
        uow_factory=object(),
        nutrition_resolver=resolver,
    )
    handler._reserve_v2_write_short = AsyncMock(
        return_value=SimpleNamespace(state="claimed")
    )

    meal = await handler.handle(command)

    assert meal.nutrition.macros.protein == pytest.approx(72)
    assert meal.nutrition.macros.fat == pytest.approx(40)
    assert meal.nutrition.calories == pytest.approx(648)
    resolver.resolve_items.assert_not_awaited()


@pytest.mark.asyncio
async def test_v2_prepared_mixed_sources_keep_confirmed_display_names():
    local_item = ManualMealItem(
        name="Bún gạo",
        quantity=180,
        unit="g",
        origin="local",
        food_reference_id=42,
        nutrition_contract_version="2",
        custom_nutrition=CustomNutrition(
            calories_per_100g=103,
            protein_per_100g=2.7,
            carbs_per_100g=43.2,
            fat_per_100g=0.4,
        ),
        source_snapshot={
            "basis": "100g",
            "canonical_name": "Rice noodles",
            "protein_per_100g": 2.7,
            "carbs_per_100g": 43.2,
            "fat_per_100g": 0.4,
        },
    )
    provider_item = ManualMealItem(
        name="Thịt bò",
        quantity=60,
        unit="g",
        origin="provider",
        source_namespace="fatsecret",
        source_food_id="fs-beef",
        nutrition_contract_version="2",
        custom_nutrition=CustomNutrition(
            calories_per_100g=282,
            protein_per_100g=15.8,
            carbs_per_100g=0,
            fat_per_100g=11.7,
        ),
        source_snapshot={
            "basis": "100g",
            "canonical_name": "Beef",
            "protein_per_100g": 15.8,
            "carbs_per_100g": 0,
            "fat_per_100g": 11.7,
        },
    )
    command = CreateManualMealCommand(
        user_id=_UUID_1,
        items=[local_item, provider_item],
        dish_name="Bún bò",
        source="prompt",
        nutrition_contract_version=2,
        idempotency_key="write-mixed",
        request_fingerprint="fingerprint-mixed",
    )
    resolver = MagicMock()
    resolver.resolve_items = AsyncMock()
    resolver.revalidate_local_items = AsyncMock()
    uow = _PreparedV2Uow()
    handler = CreateManualMealCommandHandler(
        uow=uow,
        uow_factory=object(),
        nutrition_resolver=resolver,
    )
    handler._reserve_v2_write_short = AsyncMock(
        return_value=SimpleNamespace(state="claimed")
    )
    handler._release_v2_write = AsyncMock()

    meal = await handler.handle(command)

    resolver.resolve_items.assert_not_awaited()
    assert [item.name for item in meal.nutrition.food_items] == ["Bún gạo", "Thịt bò"]
    assert meal.nutrition.food_items[0].food_reference_id == 42
    assert meal.nutrition.food_items[1].source_food_id == "fs-beef"
    assert meal.nutrition.food_items[0].source_snapshot["canonical_name"] == (
        "Rice noodles"
    )
    assert meal.nutrition.food_items[1].source_snapshot["canonical_name"] == "Beef"


class _ResolveUow:
    food_references = object()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_v2_item_missing_identity_is_rejected_with_validation_error():
    """A v2 item with no origin/id/snapshot/custom must 422, never AI-estimate."""
    item = ManualMealItem(
        name="Mystery food",
        quantity=100,
        unit="g",
        nutrition_contract_version="2",
    )
    command = CreateManualMealCommand(
        user_id=_UUID_1,
        items=[item],
        dish_name="Mystery plate",
        nutrition_contract_version=2,
        idempotency_key="write-422",
        request_fingerprint="fingerprint-422",
    )
    handler = CreateManualMealCommandHandler(
        uow=_PreparedV2Uow(),
        uow_factory=lambda: _ResolveUow(),
    )
    handler._reserve_v2_write_short = AsyncMock(
        return_value=SimpleNamespace(state="claimed")
    )
    handler._release_v2_write = AsyncMock()

    with pytest.raises(ValidationException, match="v2 item origin is required"):
        await handler.handle(command)

    handler._release_v2_write.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test B: fast (no-op) cache emits timing log without bloating elapsed time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_timing_logs_fast_cache(caplog):
    """Fast (no-op) cache completes quickly and still emits a timing log."""
    fast_cache = MagicMock()
    fast_cache.invalidate = AsyncMock()
    fast_cache.invalidate_pattern = AsyncMock()
    cache_svc = CacheInvalidationService(cache=fast_cache)

    fake_meal = _make_meal()
    handler = CreateManualMealCommandHandler(
        uow=_FakeUow(fake_meal),
        cache_invalidation=cache_svc,
    )

    cmd = _make_command(user_id=_UUID_2)

    with caplog.at_level(logging.INFO):
        result = await handler.handle(cmd)

    timing_logs = [
        r.message for r in caplog.records if "manual_save handler timing" in r.message
    ]
    assert timing_logs, (
        "No 'manual_save handler timing' log found — instrumentation missing."
    )

    assert result is fake_meal


# ---------------------------------------------------------------------------
# Test C: cache_invalidation_service emits per-family timing log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_invalidation_service_emits_timing_log(caplog):
    """after_meal_write emits a cache_invalidation timing log with critical/secondary/total."""
    fast_cache = MagicMock()
    fast_cache.invalidate = AsyncMock()
    fast_cache.invalidate_pattern = AsyncMock()
    svc = CacheInvalidationService(cache=fast_cache)

    with caplog.at_level(logging.INFO):
        await svc.after_meal_write(
            "550e8400-e29b-41d4-a716-446655440003", date(2026, 6, 10)
        )

    timing_logs = [
        r.message for r in caplog.records if "cache_invalidation timing" in r.message
    ]
    assert timing_logs, (
        "No 'cache_invalidation timing' log found in CacheInvalidationService."
    )
    assert "critical_ms" in timing_logs[0]
    assert "secondary_ms" in timing_logs[0]
    assert "total_ms" in timing_logs[0]

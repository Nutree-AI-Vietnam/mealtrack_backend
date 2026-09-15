from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.app.services.catalog_meal_snapshot_service import CatalogMealSnapshotService
from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationCatalogUnavailableError,
)
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.ports.catalog_recipe_repository_port import CatalogMealRevision
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
        from contextlib import nullcontext

        return nullcontext()

    def flush(self, *, timeout=5):
        return None


def teardown_function():
    reset_observability_connector_for_test()


class _Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class _CatalogRepo:
    def __init__(self, *, meals=None, revision=None):
        self.meals = meals or [_meal("meal-1")]
        self.revision = revision or _revision(1)
        self.revision_calls = 0
        self.load_calls = 0

    async def get_active_catalog_revision(self):
        self.revision_calls += 1
        if isinstance(self.revision, Exception):
            raise self.revision
        return self.revision

    async def list_active_meals(self):
        self.load_calls += 1
        if isinstance(self.meals, Exception):
            raise self.meals
        return self.meals


class _Uow:
    def __init__(self, catalog):
        self.catalog_recipes = catalog


@pytest.mark.asyncio
async def test_snapshot_cold_loads_once_and_warm_reuses_without_catalog_sql():
    clock = _Clock()
    catalog = _CatalogRepo()
    service = CatalogMealSnapshotService(clock=clock)

    first = await service.get_snapshot(_Uow(catalog))
    second = await service.get_snapshot(_Uow(catalog))

    assert first is second
    assert catalog.load_calls == 1
    assert catalog.revision_calls == 2


@pytest.mark.asyncio
async def test_snapshot_refreshes_when_revision_changes_after_ttl():
    clock = _Clock()
    catalog = _CatalogRepo()
    service = CatalogMealSnapshotService(ttl_seconds=10, clock=clock)

    first = await service.get_snapshot(_Uow(catalog))
    clock.value += 11
    catalog.revision = _revision(2)
    second = await service.get_snapshot(_Uow(catalog))

    assert first is not second
    assert second.revision == _revision(2)
    assert catalog.load_calls == 2


@pytest.mark.asyncio
async def test_snapshot_refreshes_when_imaged_count_changes_after_ttl():
    clock = _Clock()
    catalog = _CatalogRepo()
    service = CatalogMealSnapshotService(ttl_seconds=10, clock=clock)

    first = await service.get_snapshot(_Uow(catalog))
    clock.value += 11
    catalog.meals = [_meal("meal-1-with-image")]
    catalog.revision = CatalogMealRevision(
        active_count=1,
        catalog_updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        food_reference_updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        imaged_count=1,
    )
    second = await service.get_snapshot(_Uow(catalog))

    assert first is not second
    assert second.revision.imaged_count == 1
    assert catalog.load_calls == 2


@pytest.mark.asyncio
async def test_snapshot_extends_ttl_when_revision_is_unchanged():
    clock = _Clock()
    catalog = _CatalogRepo()
    service = CatalogMealSnapshotService(ttl_seconds=10, clock=clock)

    first = await service.get_snapshot(_Uow(catalog))
    clock.value += 11
    second = await service.get_snapshot(_Uow(catalog))

    assert second.meals == first.meals
    assert second.expires_at > first.expires_at
    assert catalog.load_calls == 1


@pytest.mark.asyncio
async def test_snapshot_cold_failure_fails_closed():
    catalog = _CatalogRepo(meals=MealRecommendationCatalogUnavailableError())
    service = CatalogMealSnapshotService(clock=_Clock())

    with pytest.raises(MealRecommendationCatalogUnavailableError):
        await service.get_snapshot(_Uow(catalog))


@pytest.mark.asyncio
async def test_snapshot_metrics_record_refresh_count_age_and_active_meals():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    clock = _Clock()
    catalog = _CatalogRepo()
    service = CatalogMealSnapshotService(clock=clock)

    await service.get_snapshot(_Uow(catalog))

    assert (
        "increment",
        "meal_catalog.snapshot.refresh",
        1.0,
        None,
        {"status": "success"},
    ) in metrics.calls
    assert any(
        call[:2] == ("gauge", "meal_catalog.snapshot.active_meals")
        and call[2] == 1
        and call[4] == {"operation": "snapshot", "status": "returned"}
        for call in metrics.calls
    )
    assert any(
        call[:2] == ("distribution", "meal_catalog.snapshot.age_seconds")
        and call[4] == {"operation": "snapshot", "status": "returned"}
        for call in metrics.calls
    )


@pytest.mark.asyncio
async def test_snapshot_last_good_metric_records_no_payload_details():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    clock = _Clock()
    catalog = _CatalogRepo()
    service = CatalogMealSnapshotService(ttl_seconds=10, clock=clock)
    await service.get_snapshot(_Uow(catalog))
    clock.value += 11
    catalog.revision = _revision(2)
    catalog.meals = RuntimeError("db unavailable")

    await service.get_snapshot(_Uow(catalog))

    assert (
        "increment",
        "meal_catalog.snapshot.last_good",
        1.0,
        None,
        {"status": "last_good"},
    ) in metrics.calls
    assert "Meal meal-1" not in str(metrics.calls)
    assert "key-meal-1" not in str(metrics.calls)


@pytest.mark.asyncio
async def test_snapshot_revision_failure_serves_last_good_snapshot():
    clock = _Clock()
    catalog = _CatalogRepo()
    service = CatalogMealSnapshotService(ttl_seconds=10, clock=clock)

    first = await service.get_snapshot(_Uow(catalog))
    clock.value += 11
    catalog.revision = RuntimeError("revision unavailable")

    second = await service.get_snapshot(_Uow(catalog))

    assert second is first
    assert catalog.load_calls == 1


def _revision(value: int) -> CatalogMealRevision:
    return CatalogMealRevision(
        active_count=value,
        catalog_updated_at=datetime(2026, 7, value, tzinfo=UTC),
        food_reference_updated_at=datetime(2026, 7, value, tzinfo=UTC),
    )


def _meal(meal_id: str) -> CatalogMeal:
    return CatalogMeal(
        id=meal_id,
        catalog_key=f"key-{meal_id}",
        content_hash=f"{meal_id:0<64}"[:64],
        name=f"Meal {meal_id}",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("20"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("breakfast",),
    )

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.exceptions import ConflictException, ResourceNotFoundException
from src.app.commands.meal_catalog import LogCatalogMealCommand
from src.app.handlers.command_handlers.meal_catalog.log_catalog_meal_command_handler import (
    LogCatalogMealCommandHandler,
)
from src.app.services.catalog_meal_log_service import (
    CatalogMealLogService,
    LogCatalogMealResult,
)
from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationAlreadyLoggedError,
)
from src.domain.model.meal_recommendation import CatalogMeal


def _catalog_meal() -> CatalogMeal:
    return CatalogMeal(
        id="catalog-1",
        catalog_key="key-1",
        content_hash="a" * 64,
        name="Egg Rice",
        cuisine="Japanese",
        description=None,
        image_url=None,
        protein_g=Decimal("20"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("2"),
        meal_types=("breakfast",),
    )


def _command(**overrides) -> LogCatalogMealCommand:
    payload = {
        "user_id": "user-1",
        "catalog_meal_id": "catalog-1",
        "meal_date": date(2026, 8, 18),
        "meal_type": "breakfast",
        "request_id": "req-1",
        "timezone": "UTC",
        "language": "en",
    }
    payload.update(overrides)
    return LogCatalogMealCommand(**payload)


def _meal(meal_id="meal-1"):
    return SimpleNamespace(
        meal_id=meal_id,
        dish_name="Egg Rice",
        nutrition=None,
        catalog_meal_id="catalog-1",
    )


def _result(*, logged_via="catalog", plan_id=None, slot_id=None, meal=None):
    saved = meal or _meal()
    return LogCatalogMealResult(
        meal_id=saved.meal_id,
        catalog_meal_id="catalog-1",
        logged_via=logged_via,
        plan_id=plan_id,
        slot_id=slot_id,
        meal_date=date(2026, 8, 18),
        meal_type="breakfast",
        meal=saved,
    )


class _Reservation:
    def __init__(self, state="acquired", response=None):
        self.state = state
        self.response = response
        self.operation_id = "op-1"


class _WriteOps:
    def __init__(self, reservation=None):
        self._reservation = reservation or _Reservation()
        self.reserve = AsyncMock(return_value=self._reservation)
        self.complete = AsyncMock()
        self.release = AsyncMock()


class _Plans:
    def __init__(self, match=None):
        self.find_logable_slot_for_catalog_meal = AsyncMock(return_value=match)
        self.claim_slot_log = AsyncMock()
        self.finalize_slot_logged = AsyncMock()


class _Uow:
    def __init__(self, writes=None, plans=None, meals=None):
        self.meal_write_operations = writes or _WriteOps()
        self.meal_recommendation_plans = plans or _Plans()
        self.meals = meals or SimpleNamespace(find_by_id=AsyncMock())

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _Browse:
    def __init__(self, meal=None, missing=False):
        self.missing = missing
        self.meal = meal or _catalog_meal()
        self.get_meal = AsyncMock(side_effect=self._get)

    async def _get(self, catalog_id):
        if self.missing:
            raise KeyError(catalog_id)
        return self.meal


def _handler(
    uow,
    browse,
    log_service,
    event_publisher=None,
    recalculator=None,
    translation=None,
):
    return LogCatalogMealCommandHandler(
        uow=uow,
        browse_service=browse,
        log_service=log_service,
        meal_translation_service=translation,
        event_publisher=event_publisher,
        recalculator=recalculator,
    )


@pytest.mark.asyncio
async def test_unknown_catalog_id_is_404_without_write():
    writes = _WriteOps()
    handler = _handler(_Uow(writes=writes), _Browse(missing=True), AsyncMock())

    with pytest.raises(ResourceNotFoundException):
        await handler.handle(_command())

    writes.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefer_slot_logs_matching_unlogged_slot():
    log_service = AsyncMock()
    log_service.execute = AsyncMock(
        return_value=_result(logged_via="slot", plan_id="plan-1", slot_id="slot-1")
    )
    recalculator = SimpleNamespace(recalculate=AsyncMock())

    publisher = MagicMock()
    publisher.publish = AsyncMock()
    handler = _handler(
        _Uow(),
        _Browse(),
        log_service,
        event_publisher=publisher,
        recalculator=recalculator,
    )

    result = await handler.handle(_command())

    assert result.logged_via == "slot"
    assert result.plan_id == "plan-1"
    publisher.publish.assert_awaited_once()
    recalculator.recalculate.assert_awaited_once()


@pytest.mark.asyncio
async def test_translation_is_called_when_language_provided():
    translated: list[str] = []
    log_service = AsyncMock()
    log_service.execute = AsyncMock(return_value=_result())

    class _Translation:
        async def translate_meal(self, **kwargs):
            translated.append("translate")

    handler = _handler(
        _Uow(),
        _Browse(),
        log_service,
        translation=_Translation(),
    )

    await handler.handle(_command(language="vi"))

    assert translated == ["translate"]


@pytest.mark.asyncio
async def test_recalculate_is_called_when_present():
    log_service = AsyncMock()
    log_service.execute = AsyncMock(
        return_value=_result(logged_via="slot", plan_id="plan-1", slot_id="slot-1")
    )
    recalculator = SimpleNamespace(recalculate=AsyncMock())

    handler = _handler(
        _Uow(),
        _Browse(),
        log_service,
        recalculator=recalculator,
    )

    result = await handler.handle(_command())

    assert result.meal_id == "meal-1"
    recalculator.recalculate.assert_awaited_once()


@pytest.mark.asyncio
async def test_standalone_when_no_matching_slot():
    log_service = AsyncMock()
    log_service.execute = AsyncMock(return_value=_result(logged_via="catalog"))
    handler = _handler(_Uow(), _Browse(), log_service)

    result = await handler.handle(_command(meal_type="lunch"))

    assert result.logged_via == "catalog"
    assert result.plan_id is None


@pytest.mark.asyncio
async def test_replay_returns_stored_body_without_second_log():
    payload = _result().to_replay_payload()
    writes = _WriteOps(_Reservation(state="replay", response=payload))
    log_service = AsyncMock()
    log_service.execute = AsyncMock()
    handler = _handler(_Uow(writes=writes), _Browse(), log_service)

    result = await handler.handle(_command())

    assert result.meal_id == "meal-1"
    log_service.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_replay_payload_is_409_not_key_error():
    writes = _WriteOps(_Reservation(state="replay", response={"meal_id": "meal-1"}))
    handler = _handler(_Uow(writes=writes), _Browse(), AsyncMock())

    with pytest.raises(ConflictException) as exc_info:
        await handler.handle(_command())

    assert exc_info.value.error_code == "IDEMPOTENCY_REPLAY_INVALID"


@pytest.mark.asyncio
async def test_fingerprint_conflict_is_409():
    writes = _WriteOps(_Reservation(state="fingerprint_conflict"))
    handler = _handler(_Uow(writes=writes), _Browse(), AsyncMock())

    with pytest.raises(ConflictException):
        await handler.handle(_command())


@pytest.mark.asyncio
async def test_service_falls_through_when_claim_already_logged():
    plans = _Plans(match=("plan-1", "slot-1"))
    plans.claim_slot_log = AsyncMock(side_effect=MealRecommendationAlreadyLoggedError)
    materializer = SimpleNamespace(
        materialize=AsyncMock(),
        materialize_from_catalog=AsyncMock(return_value=_meal("meal-2")),
    )
    uow = _Uow(plans=plans)
    service = CatalogMealLogService(materializer=materializer)

    result = await service.execute(uow, _command(), _catalog_meal())

    assert result.logged_via == "catalog"
    assert result.meal_id == "meal-2"
    materializer.materialize.assert_not_awaited()

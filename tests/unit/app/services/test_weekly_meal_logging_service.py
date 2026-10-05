from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_planner import LogMealPlanSlotCommand
from src.app.services.weekly_meal_logging_service import WeeklyMealLoggingService

pytestmark = pytest.mark.usefixtures("planner_flags_off")


class _MockReservation:
    def __init__(self, state="new", response=None):
        self.state = state
        self.response = response


class _MockWriteOperations:
    def __init__(self):
        self.completed = False
        self.released = False

    async def reserve(self, **kwargs):
        return _MockReservation()

    async def complete(self, reservation, **kwargs):
        self.completed = True

    async def release(self, reservation):
        self.released = True


class _MockCatalogRecipes:
    def __init__(self, meal=None):
        self.meal = meal

    async def get_meal(self, catalog_meal_id):
        return self.meal


class _MockMaterializer:
    async def materialize_from_catalog(self, uow, **kwargs):
        return SimpleNamespace(
            meal_id="materialized-meal-123",
            nutrition=SimpleNamespace(
                macros=SimpleNamespace(
                    total_calories=550.0,
                )
            ),
        )


def _make_command(
    *,
    plan_id="plan-1",
    slot_id="slot-0-0",
    meal_date=date(2026, 9, 21),
    meal_type="breakfast",
    expected_recipe_id="recipe-1",
    portion_multiplier=1.0,
):
    return LogMealPlanSlotCommand(
        user_id="user-1",
        plan_id=plan_id,
        slot_id=slot_id,
        idempotency_key="idem-key-1",
        meal_date=meal_date,
        meal_type=meal_type,
        expected_recipe_id=expected_recipe_id,
        portion_multiplier=portion_multiplier,
        timezone="UTC",
    )


@pytest.mark.asyncio
async def test_slot_log_happy_path_with_eager_plan_optimizes_roundtrips():
    plan_obj = SimpleNamespace(
        id="plan-1",
        week_start_date=date(2026, 9, 21),
    )
    slot_obj = SimpleNamespace(
        id="slot-0-0",
        day_index=0,
        slot_index=0,
        catalog_meal_id="recipe-1",
        is_logged=False,
        plan=plan_obj,
    )
    catalog_meal = SimpleNamespace(id="recipe-1", name="Phở bò")

    mock_weekly_plans = SimpleNamespace(
        get_slot_for_update=AsyncMock(return_value=slot_obj),
        get_by_id=AsyncMock(),
        mark_slot_logged=AsyncMock(),
        plan_exists=AsyncMock(return_value=True),
    )
    mock_catalog = _MockCatalogRecipes(meal=catalog_meal)
    mock_ops = _MockWriteOperations()

    uow = SimpleNamespace(
        weekly_meal_plans=mock_weekly_plans,
        catalog_recipes=mock_catalog,
        meal_write_operations=mock_ops,
    )

    class _UowFactory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = WeeklyMealLoggingService(_UowFactory, materializer=_MockMaterializer())
    cmd = _make_command()

    result = await service.log(cmd)

    assert result.logged_meal_id == "materialized-meal-123"
    assert result.slot_id == "slot-0-0"
    assert result.calories == 550.0

    # get_slot_for_update was called
    mock_weekly_plans.get_slot_for_update.assert_awaited_once_with(
        user_id="user-1", plan_id="plan-1", slot_id="slot-0-0"
    )
    # get_by_id was NOT called because plan was eagerly attached to slot!
    mock_weekly_plans.get_by_id.assert_not_called()
    # mark_slot_logged received the slot instance directly
    mock_weekly_plans.mark_slot_logged.assert_awaited_once_with(
        user_id="user-1",
        plan_id="plan-1",
        slot_id="slot-0-0",
        meal_id="materialized-meal-123",
        slot=slot_obj,
    )
    assert mock_ops.completed is True


@pytest.mark.asyncio
async def test_slot_log_plan_not_found_raises_not_found():
    mock_weekly_plans = SimpleNamespace(
        get_slot_for_update=AsyncMock(return_value=None),
        plan_exists=AsyncMock(return_value=False),
        get_by_id=AsyncMock(return_value=None),
    )
    uow = SimpleNamespace(
        weekly_meal_plans=mock_weekly_plans,
        meal_write_operations=_MockWriteOperations(),
    )

    class _UowFactory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = WeeklyMealLoggingService(_UowFactory, materializer=_MockMaterializer())
    cmd = _make_command()

    with pytest.raises(ResourceNotFoundException, match="Weekly meal plan not found"):
        await service.log(cmd)


@pytest.mark.asyncio
async def test_slot_log_slot_not_found_raises_not_found():
    mock_weekly_plans = SimpleNamespace(
        get_slot_for_update=AsyncMock(return_value=None),
        plan_exists=AsyncMock(return_value=True),
    )
    uow = SimpleNamespace(
        weekly_meal_plans=mock_weekly_plans,
        meal_write_operations=_MockWriteOperations(),
    )

    class _UowFactory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = WeeklyMealLoggingService(_UowFactory, materializer=_MockMaterializer())
    cmd = _make_command()

    with pytest.raises(ResourceNotFoundException, match="Weekly meal slot not found"):
        await service.log(cmd)


@pytest.mark.asyncio
async def test_slot_log_already_logged_conflict():
    plan_obj = SimpleNamespace(
        id="plan-1",
        week_start_date=date(2026, 9, 21),
    )
    slot_obj = SimpleNamespace(
        id="slot-0-0",
        day_index=0,
        slot_index=0,
        catalog_meal_id="recipe-1",
        is_logged=True,
        plan=plan_obj,
    )

    mock_weekly_plans = SimpleNamespace(
        get_slot_for_update=AsyncMock(return_value=slot_obj),
    )
    uow = SimpleNamespace(
        weekly_meal_plans=mock_weekly_plans,
        meal_write_operations=_MockWriteOperations(),
    )

    class _UowFactory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = WeeklyMealLoggingService(_UowFactory, materializer=_MockMaterializer())
    cmd = _make_command()

    with pytest.raises(ConflictException) as exc:
        await service.log(cmd)
    assert exc.value.error_code == "WEEKLY_SLOT_ALREADY_LOGGED"


@pytest.mark.asyncio
async def test_slot_log_date_mismatch_raises_validation_error():
    plan_obj = SimpleNamespace(
        id="plan-1",
        week_start_date=date(2026, 9, 21),
    )
    slot_obj = SimpleNamespace(
        id="slot-0-0",
        day_index=0,
        slot_index=0,
        catalog_meal_id="recipe-1",
        is_logged=False,
        plan=plan_obj,
    )

    mock_weekly_plans = SimpleNamespace(
        get_slot_for_update=AsyncMock(return_value=slot_obj),
    )
    uow = SimpleNamespace(
        weekly_meal_plans=mock_weekly_plans,
        meal_write_operations=_MockWriteOperations(),
    )

    class _UowFactory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = WeeklyMealLoggingService(_UowFactory, materializer=_MockMaterializer())
    # date is Tuesday 2026-09-22 instead of Monday 2026-09-21 for day_index=0
    cmd = _make_command(meal_date=date(2026, 9, 22))

    with pytest.raises(ValidationException) as exc:
        await service.log(cmd)
    assert exc.value.error_code == "WEEKLY_SLOT_MISMATCH"


@pytest.mark.asyncio
async def test_slot_log_falls_back_to_plan_timezone_when_omitted():
    plan_obj = SimpleNamespace(
        id="plan-1",
        week_start_date=date(2026, 9, 21),
        timezone="Asia/Ho_Chi_Minh",
    )
    slot_obj = SimpleNamespace(
        id="slot-0-0",
        day_index=0,
        slot_index=0,
        catalog_meal_id="recipe-1",
        is_logged=False,
        plan=plan_obj,
    )
    catalog_meal = SimpleNamespace(id="recipe-1", name="Phở bò")

    mock_weekly_plans = SimpleNamespace(
        get_slot_for_update=AsyncMock(return_value=slot_obj),
        get_by_id=AsyncMock(),
        mark_slot_logged=AsyncMock(),
        plan_exists=AsyncMock(return_value=True),
    )
    mock_catalog = _MockCatalogRecipes(meal=catalog_meal)
    mock_ops = _MockWriteOperations()

    captured_kwargs = {}

    class _CaptureMaterializer:
        async def materialize_from_catalog(self, uow, **kwargs):
            captured_kwargs.update(kwargs)
            return SimpleNamespace(
                meal_id="meal-tz-test",
                nutrition=SimpleNamespace(
                    macros=SimpleNamespace(
                        total_calories=500.0,
                    )
                ),
            )

    uow = SimpleNamespace(
        weekly_meal_plans=mock_weekly_plans,
        catalog_recipes=mock_catalog,
        meal_write_operations=mock_ops,
    )

    class _UowFactory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = WeeklyMealLoggingService(_UowFactory, materializer=_CaptureMaterializer())
    cmd = LogMealPlanSlotCommand(
        user_id="user-1",
        plan_id="plan-1",
        slot_id="slot-0-0",
        idempotency_key="idem-key-tz",
        meal_date=date(2026, 9, 21),
        meal_type="breakfast",
        expected_recipe_id="recipe-1",
        portion_multiplier=1.0,
        timezone=None,
    )

    result = await service.log(cmd)

    assert result.logged_meal_id == "meal-tz-test"
    assert captured_kwargs.get("timezone") == "Asia/Ho_Chi_Minh"


@pytest.mark.asyncio
async def test_slot_log_falls_back_to_fetched_plan_timezone_when_slot_plan_not_eagerly_loaded():
    plan_obj = SimpleNamespace(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        timezone="Asia/Tokyo",
    )
    slot_obj = SimpleNamespace(
        id="slot-0-0",
        day_index=0,
        slot_index=0,
        catalog_meal_id="recipe-1",
        is_logged=False,
        plan=None,  # NOT eagerly loaded!
    )
    catalog_meal = SimpleNamespace(id="recipe-1", name="Phở bò")

    mock_weekly_plans = SimpleNamespace(
        get_slot_for_update=AsyncMock(return_value=slot_obj),
        get_by_id=AsyncMock(return_value=plan_obj),
        mark_slot_logged=AsyncMock(),
        plan_exists=AsyncMock(return_value=True),
    )
    mock_catalog = _MockCatalogRecipes(meal=catalog_meal)
    mock_ops = _MockWriteOperations()

    captured_kwargs = {}

    class _CaptureMaterializer:
        async def materialize_from_catalog(self, uow, **kwargs):
            captured_kwargs.update(kwargs)
            return SimpleNamespace(
                meal_id="meal-tz-test-fallback",
                nutrition=SimpleNamespace(
                    macros=SimpleNamespace(
                        total_calories=500.0,
                    )
                ),
            )

    uow = SimpleNamespace(
        weekly_meal_plans=mock_weekly_plans,
        catalog_recipes=mock_catalog,
        meal_write_operations=mock_ops,
    )

    class _UowFactory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, exc_type, exc, tb):
            return False

    service = WeeklyMealLoggingService(_UowFactory, materializer=_CaptureMaterializer())
    cmd = LogMealPlanSlotCommand(
        user_id="user-1",
        plan_id="plan-1",
        slot_id="slot-0-0",
        idempotency_key="idem-key-tz-fallback",
        meal_date=date(2026, 9, 21),
        meal_type="breakfast",
        expected_recipe_id="recipe-1",
        portion_multiplier=1.0,
        timezone=None,
    )

    result = await service.log(cmd)

    assert result.logged_meal_id == "meal-tz-test-fallback"
    assert captured_kwargs.get("timezone") == "Asia/Tokyo"

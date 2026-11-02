"""Weekly slot log, unlog, link and skip against real PostgreSQL."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from tests.integration.postgres.test_weekly_planner_optimization import WEEK, _seed

from src.app.commands.meal import DeleteMealCommand
from src.app.commands.meal_planner import (
    LinkMealPlanSlotCommand,
    LogMealPlanSlotCommand,
    SetMealPlanSlotSkipCommand,
    UnlogMealPlanSlotCommand,
)
from src.app.handlers.command_handlers.delete_meal_command_handler import (
    DeleteMealCommandHandler,
)
from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.app.services.weekly_grocery_service import WeeklyGroceryService
from src.app.services.weekly_meal_logging_service import WeeklyMealLoggingService
from src.app.services.weekly_meal_slot_state_service import (
    WeeklyMealSlotStateService,
)
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.uow_async import AsyncUnitOfWork

pytestmark = pytest.mark.integration


class _Publisher:
    def __init__(self):
        self.payloads = []

    async def publish(self, payload):
        self.payloads.append(payload)


@pytest.fixture
def uow_on_test_db(async_session_factory, monkeypatch):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    return AsyncUnitOfWork


async def _slot(user_id, plan_id, slot_id):
    async with AsyncUnitOfWork() as uow:
        plan = await uow.weekly_meal_plans.get_by_id(user_id=user_id, plan_id=plan_id)
    return next(slot for slot in plan.slots if slot.id == slot_id)


async def _meal_exists(meal_id) -> bool:
    async with AsyncUnitOfWork() as uow:
        result = await uow.session.execute(
            select(MealORM.meal_id).where(MealORM.meal_id == meal_id)
        )
        return result.scalar_one_or_none() is not None


async def _log(user_id, plan, slot):
    return await WeeklyMealLoggingService(AsyncUnitOfWork).log(
        LogMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=slot.id,
            idempotency_key=str(uuid4()),
            meal_date=WEEK + timedelta(days=slot.day_index),
            meal_type=("breakfast", "lunch", "dinner")[slot.slot_index],
            expected_recipe_id=slot.recipe_id,
            portion_multiplier=1.0,
            timezone="UTC",
        )
    )


@pytest.mark.asyncio
async def test_deleting_a_logged_meal_releases_its_slot(pg_session, uow_on_test_db):
    user_id, plan, _ = await _seed(pg_session)
    slot = plan.slots[0]
    logged = await _log(user_id, plan, slot)
    assert (await _slot(user_id, plan.id, slot.id)).log_source == "plan"

    await DeleteMealCommandHandler(
        uow_factory=AsyncUnitOfWork, event_publisher=_Publisher()
    ).handle(DeleteMealCommand(meal_id=logged.logged_meal_id, user_id=user_id))

    released = await _slot(user_id, plan.id, slot.id)
    assert (released.is_logged, released.logged_meal_id, released.log_source) == (
        False,
        None,
        None,
    )
    # The slot can be logged again instead of answering 409.
    relogged = await _log(user_id, plan, slot)
    assert (await _slot(user_id, plan.id, slot.id)).logged_meal_id == (
        relogged.logged_meal_id
    )


@pytest.mark.asyncio
async def test_unlog_deletes_plan_meal_but_keeps_linked_diary_meal(
    pg_session, uow_on_test_db
):
    user_id, plan, _ = await _seed(pg_session)
    breakfast, lunch = plan.slots[0], plan.slots[1]
    publisher = _Publisher()
    service = WeeklyMealSlotStateService(AsyncUnitOfWork, event_publisher=publisher)

    logged = await _log(user_id, plan, breakfast)
    unlogged = await service.unlog(
        UnlogMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=breakfast.id,
            idempotency_key=str(uuid4()),
        )
    )
    assert not next(s for s in unlogged.slots if s.id == breakfast.id).is_logged
    assert not await _meal_exists(logged.logged_meal_id)
    assert [p["event_type"] for p in publisher.payloads] == ["meal.deleted.v1"]

    async with AsyncUnitOfWork() as uow:
        catalog_meal = await uow.catalog_recipes.get_meal(lunch.recipe_id)
        diary_meal = (
            await RecommendedMealMaterializationService().materialize_from_catalog(
                uow,
                user_id=user_id,
                catalog_meal=catalog_meal,
                meal_date=WEEK,
                meal_type="lunch",
                timezone="UTC",
                source="manual",
            )
        )
    linked = await service.link(
        LinkMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=lunch.id,
            idempotency_key=str(uuid4()),
            meal_id=diary_meal.meal_id,
        )
    )
    linked_slot = next(s for s in linked.slots if s.id == lunch.id)
    assert (linked_slot.logged_meal_id, linked_slot.log_source) == (
        diary_meal.meal_id,
        "diary",
    )

    await service.unlog(
        UnlogMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=lunch.id,
            idempotency_key=str(uuid4()),
        )
    )
    assert not (await _slot(user_id, plan.id, lunch.id)).is_logged
    assert await _meal_exists(diary_meal.meal_id)


@pytest.mark.asyncio
async def test_skipped_slot_leaves_groceries_and_replays(pg_session, uow_on_test_db):
    user_id, plan, _ = await _seed(pg_session)
    service = WeeklyMealSlotStateService(AsyncUnitOfWork)

    async def rice_needed():
        async with AsyncUnitOfWork() as uow:
            current = await uow.weekly_meal_plans.get_by_id(
                user_id=user_id, plan_id=plan.id
            )
            categories = await WeeklyGroceryService().calculate(uow, current)
        return sum(item.total_needed for c in categories for item in c.items)

    before = await rice_needed()
    command = SetMealPlanSlotSkipCommand(
        user_id=user_id,
        plan_id=plan.id,
        slot_id=plan.slots[2].id,
        idempotency_key="skip-dinner",
        skipped=True,
    )
    skipped = await service.set_skip(command)
    replayed = await service.set_skip(command)

    assert next(s for s in skipped.slots if s.id == plan.slots[2].id).is_skipped
    assert replayed.slots == skipped.slots
    assert await rice_needed() < before
    # Logging the skipped slot ends the skip.
    await _log(user_id, plan, plan.slots[2])
    assert not (await _slot(user_id, plan.id, plan.slots[2].id)).is_skipped

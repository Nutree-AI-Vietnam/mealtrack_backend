"""Fresh-session query and mutation invariants on an isolated PostgreSQL DB."""

import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event

from src.app.services.catalog_meal_seed_import_service import CatalogMealSeedImporter
from src.app.services.weekly_grocery_service import WeeklyGroceryService
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.infra.database.models.enums import MealStatusEnum
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.user.user import User
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)
from src.infra.repositories.weekly_meal_plan_repository_async import (
    AsyncWeeklyMealPlanRepository,
)

pytestmark = pytest.mark.integration
WEEK = date(2026, 9, 21)


async def _seed(session, *, diverse=False):
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            firebase_uid=user_id,
            email=f"{user_id}@example.test",
            username=user_id,
            password_hash="test",
        )
    )
    food_repo = AsyncFoodReferenceRepository(session)
    reference = await food_repo.upsert_by_normalized_name(
        name="Rice",
        name_normalized="rice",
        protein_100g=2.7,
        carbs_100g=28.0,
        fat_100g=0.3,
        fiber_100g=0.4,
        sugar_100g=0.1,
        source="catalog_seed",
        is_verified=True,
    )
    catalog = AsyncCatalogMealRepository(session)
    await CatalogMealSeedImporter(catalog, food_repo).import_manifest(
        {
            "recipes": [
                {
                    "recipe_key": f"rice-{index}",
                    "name": f"Rice {index}",
                    "cuisine": "vietnamese",
                    "base_servings": 1,
                    "serving_confidence": "verified",
                    "meal_types": ["lunch", "dinner"],
                    "ingredients": [
                        {
                            "food_reference_id": reference["id"],
                            "name": "Rice",
                            "quantity": 100 + index,
                            "unit": "g",
                        }
                    ],
                }
                for index in range(2)
            ]
        }
    )
    meals = sorted(await catalog.list_active_meals(), key=lambda meal: meal.catalog_key)
    plan = await AsyncWeeklyMealPlanRepository(session).create(
        user_id=user_id,
        week_start_date=WEEK,
        timezone="UTC",
        preferences=WeeklyMealPlanPreferences(),
        recipe_ids={
            (day, slot): meals[(day + slot) % 2 if diverse else 0].id
            for day in range(7)
            for slot in range(2)
        },
    )
    await session.commit()
    return user_id, plan, reference["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("diverse", [False, True])
async def test_plan_read_uses_two_core_selects_in_fresh_session(
    pg_session, async_session_factory, diverse
):
    user_id, original, _ = await _seed(pg_session, diverse=diverse)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    async with async_session_factory() as session:
        engine = session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            plan = await AsyncWeeklyMealPlanRepository(session).get_by_id(
                user_id=user_id, plan_id=original.id
            )
            assert plan == original
            assert len(statements) == 2
            assert all(
                "meal_catalog" not in sql and "pantry" not in sql for sql in statements
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)


@pytest.mark.asyncio
@pytest.mark.parametrize("stock", [0, 50, 2000])
async def test_pantry_flags_day_lines_and_undo_survive_fresh_reads(
    pg_session, async_session_factory, stock
):
    user_id, plan, food_id = await _seed(pg_session)
    async with async_session_factory() as session:
        repo = AsyncWeeklyMealPlanRepository(session)
        await repo.update_pantry(
            user_id=user_id,
            plan_id=plan.id,
            updates=[
                {
                    "ingredient_id": food_id,
                    "available_amount": stock,
                    "available_unit": "g",
                    "checked": True,
                    "do_not_buy": True,
                    "manually_owned": True,
                }
            ],
        )
        await repo.replace_grocery_day_lines(
            user_id=user_id,
            plan_id=plan.id,
            ingredient_id=food_id,
            lines=[{"day_index": 0, "needed_amount": 25, "covered": True}],
        )
        await session.commit()
    async with async_session_factory() as session:
        repo = AsyncWeeklyMealPlanRepository(session)
        loaded = await repo.get_by_id(user_id=user_id, plan_id=plan.id)
        items = await WeeklyGroceryService().calculate(
            SimpleNamespace(
                weekly_meal_plans=repo,
                catalog_recipes=AsyncCatalogMealRepository(session),
            ),
            loaded,
        )
        item = items[0].items[0]
        assert item.stock_amount == stock
        assert item.total_needed == 1400
        assert item.remaining == max(1400 - stock, 0)
        assert item.status == (
            "owned" if stock >= 1400 else "needed" if stock == 0 else "need_more"
        )
        assert item.checked and item.do_not_buy and item.manually_owned
        assert item.day_notes[0]["covered"]
        assert await repo.get_by_id(user_id="unauthorized", plan_id=plan.id) is None
        assert await repo.list_pantry(user_id="unauthorized", plan_id=plan.id) == []
    async with async_session_factory() as session:
        repo = AsyncWeeklyMealPlanRepository(session)
        await repo.update_pantry(
            user_id=user_id,
            plan_id=plan.id,
            updates=[
                {
                    "ingredient_id": food_id,
                    "available_amount": 0,
                    "available_unit": "g",
                    "checked": False,
                    "do_not_buy": False,
                    "manually_owned": False,
                }
            ],
        )
        await repo.replace_grocery_day_lines(
            user_id=user_id, plan_id=plan.id, ingredient_id=food_id, lines=[]
        )
        await session.commit()
    async with async_session_factory() as session:
        repo = AsyncWeeklyMealPlanRepository(session)
        assert (await repo.list_pantry(user_id=user_id, plan_id=plan.id))[0][
            "available_amount"
        ] == 0
        interaction = (
            await repo.list_grocery_interactions(user_id=user_id, plan_id=plan.id)
        )[0]
        assert not any(
            interaction[flag] for flag in ("checked", "do_not_buy", "manually_owned")
        )
        assert await repo.list_grocery_day_lines(user_id=user_id, plan_id=plan.id) == []


@pytest.mark.asyncio
async def test_concurrent_same_revision_swaps_allow_one_commit(
    pg_session, async_session_factory
):
    user_id, plan, _ = await _seed(pg_session)
    initially_empty = sum(slot.recipe_id is None for slot in plan.slots)

    async def swap(coordinate):
        async with async_session_factory() as session:
            try:
                await AsyncWeeklyMealPlanRepository(session).update(
                    user_id=user_id,
                    plan_id=plan.id,
                    slots={coordinate: None},
                    expected_revision=plan.revision,
                )
                await session.commit()
                return "updated"
            except WeeklyMealPlanConflictError as exc:
                await session.rollback()
                return exc.error_code

    assert sorted(
        await asyncio.wait_for(asyncio.gather(swap((0, 0)), swap((0, 1))), 5)
    ) == ["WEEKLY_PLAN_STALE_REVISION", "updated"]
    async with async_session_factory() as session:
        loaded = await AsyncWeeklyMealPlanRepository(session).get_by_id(
            user_id=user_id, plan_id=plan.id
        )
        assert loaded.revision == plan.revision + 1
        assert (
            sum(slot.recipe_id is None for slot in loaded.slots) == initially_empty + 1
        )


@pytest.mark.asyncio
async def test_log_then_waiting_swap_rejects_logged_slot(
    pg_session, async_session_factory
):
    user_id, plan, _ = await _seed(pg_session)
    meal_id = str(uuid4())
    pg_session.add(
        MealORM(meal_id=meal_id, user_id=user_id, status=MealStatusEnum.READY)
    )
    await pg_session.commit()
    async with async_session_factory() as logging_session:
        repo = AsyncWeeklyMealPlanRepository(logging_session)
        slot = await repo.get_slot_for_update(
            user_id=user_id, plan_id=plan.id, slot_id=plan.slots[0].id
        )

        async def swap():
            async with async_session_factory() as session:
                with pytest.raises(WeeklyMealPlanConflictError, match="Logged"):
                    await AsyncWeeklyMealPlanRepository(session).update(
                        user_id=user_id,
                        plan_id=plan.id,
                        slots={(0, 0): None},
                        expected_revision=plan.revision,
                    )

        task = asyncio.create_task(swap())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.1)
        await repo.mark_slot_logged(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=slot.id,
            meal_id=meal_id,
            slot=slot,
        )
        await logging_session.commit()
        await asyncio.wait_for(task, 5)
    async with async_session_factory() as session:
        loaded = await AsyncWeeklyMealPlanRepository(session).get_by_id(
            user_id=user_id, plan_id=plan.id
        )
        assert loaded.slots[0].is_logged and loaded.slots[0].logged_meal_id == meal_id
        assert loaded.slots[0].recipe_id == plan.slots[0].recipe_id


@pytest.mark.asyncio
async def test_missing_week_lock_serializes_creation(pg_session, async_session_factory):
    user_id, _, _ = await _seed(pg_session)
    missing_week = date(2026, 9, 28)

    async def create():
        async with async_session_factory() as session:
            repo = AsyncWeeklyMealPlanRepository(session)
            await repo.lock_user_week(user_id=user_id, week_start_date=missing_week)
            plan = await repo.get_current(user_id=user_id, week_start_date=missing_week)
            if plan is None:
                plan = await repo.create(
                    user_id=user_id,
                    week_start_date=missing_week,
                    timezone="UTC",
                    preferences=WeeklyMealPlanPreferences(),
                )
            await session.commit()
            return plan.id

    first, second = await asyncio.wait_for(asyncio.gather(create(), create()), 5)
    assert first == second

"""Saved (imported) recipes in weekly plan slots against real PostgreSQL."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from tests.integration.postgres.test_weekly_planner_optimization import WEEK, _seed

from src.api.exceptions import ResourceNotFoundException
from src.api.routes.v1.meal_plans import _plan_response
from src.app.commands.meal_planner import (
    LinkMealPlanSlotCommand,
    LogMealPlanSlotCommand,
    SetMealPlanSlotSkipCommand,
    UnlogMealPlanSlotCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.app.commands.saved_suggestion import DeleteSavedSuggestionCommand
from src.app.handlers.command_handlers.saved_suggestion import (
    DeleteSavedSuggestionCommandHandler,
)
from src.app.handlers.query_handlers.meal_planner.weekly_meal_planner_query_handlers import (
    GetRecipeSummariesQueryHandler,
    GetWeeklyGroceriesQueryHandler,
)
from src.app.services.recommended_meal_materialization_service import (
    RecommendedMealMaterializationService,
)
from src.app.services.weekly_grocery_service import WeeklyGroceryService
from src.app.services.weekly_meal_logging_service import WeeklyMealLoggingService
from src.app.services.weekly_meal_plan_service import WeeklyMealPlanService
from src.app.services.weekly_meal_slot_state_service import (
    WeeklyMealSlotStateService,
)
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
)
from src.domain.model.weekly_meal_planner import SavedRecipeSnapshot
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.user.user import User
from src.infra.database.models.weekly_meal_planner import WeeklyMealPlanSlotORM
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.repositories.saved_suggestion_db_repository_async import (
    AsyncSavedSuggestionDbRepository,
)

pytestmark = pytest.mark.integration

SAVED_ID = "recipe_import_7d1c2f8e-0b7a-4a52-9a43-2f0f6b0f1a11"
DINNER = (1, 2)


class _Publisher:
    def __init__(self):
        self.payloads = []

    async def publish(self, payload):
        self.payloads.append(payload)


class _Bus:
    """The plan payload's two reads, through the real handlers."""

    def __init__(self):
        self.summaries = GetRecipeSummariesQueryHandler(AsyncUnitOfWork)
        self.groceries = GetWeeklyGroceriesQueryHandler(AsyncUnitOfWork)

    async def send(self, query):
        name = query.__class__.__name__
        if name == "GetRecipeSummariesQuery":
            return await self.summaries.handle(query)
        if name == "GetWeeklyGroceriesQuery":
            return await self.groceries.handle(query)
        raise AssertionError(f"unexpected query {name}")


@pytest.fixture
def uow_on_test_db(async_session_factory, monkeypatch):
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    return AsyncUnitOfWork


def _import_payload():
    """suggestion_data as POST /v1/recipe-imports/{id}/save stores it."""
    return {
        "id": SAVED_ID,
        "meal_name": "Grandma's chicken pho",
        "description": "Clear broth",
        "macros": {
            "calories": 402,
            "protein": 30.0,
            "carbs": 50.0,
            "fat": 10.0,
            "fiber": 4.0,
            "sugar": 2.0,
        },
        "ingredients": [
            {
                "name": "Rice noodles",
                "amount": 400.0,
                "unit": "g",
                "grams": 400.0,
                "food_reference_id": None,
                "protein": 10.8,
                "carbs": 112.0,
                "fat": 1.2,
            },
            {
                "name": "Chicken thigh",
                "amount": 600.0,
                "unit": "g",
                "grams": 600.0,
                "food_reference_id": None,
                "protein": 110.0,
                "carbs": 0.0,
                "fat": 38.0,
            },
            {"name": "Eggs", "amount": 4.0, "unit": "", "grams": 200.0},
        ],
        "recipe_steps": [
            {"step": 1, "instruction": "Simmer the broth.", "duration_minutes": 40}
        ],
        "prep_time_minutes": 60,
        "image_url": "https://images.example/pho.jpg",
        "servings": 4,
        "recipe_import": {
            "platform": "website",
            "source_url": "https://cook.example/pho",
            "source_author": "Grandma",
            "original_servings": 4,
            "servings_estimated": False,
            "total_time_minutes": 60,
        },
    }


async def _add_user(session) -> str:
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
    await session.flush()
    return user_id


async def _seed_with_saved_recipe(session):
    user_id, plan, rice_id = await _seed(session)
    await AsyncSavedSuggestionDbRepository(session).save(
        user_id=user_id,
        suggestion_id=SAVED_ID,
        meal_type="dinner",
        portion_multiplier=1,
        suggestion_data=_import_payload(),
    )
    await session.commit()
    return user_id, plan, rice_id


async def _put_saved(user_id, plan, *, key=None, coordinate=DINNER, revision=None):
    return await WeeklyMealPlanService(AsyncUnitOfWork).update(
        UpdateWeeklyMealPlanCommand(
            user_id=user_id,
            plan_id=plan.id,
            idempotency_key=key or str(uuid4()),
            slots={},
            expected_revision=revision if revision is not None else plan.revision,
            saved_slots={coordinate: SAVED_ID},
        )
    )


async def _current(user_id, plan_id):
    async with AsyncUnitOfWork() as uow:
        return await uow.weekly_meal_plans.get_by_id(user_id=user_id, plan_id=plan_id)


def _slot_at(plan, coordinate):
    return next(
        slot for slot in plan.slots if (slot.day_index, slot.slot_index) == coordinate
    )


async def _payload(plan):
    response = await _plan_response(plan, _Bus(), localization_timeout_seconds=None)
    return response.model_dump(mode="json")


def _payload_slot(body, coordinate):
    return body["plan"][coordinate[0]][coordinate[1]]


async def _log(user_id, plan, slot, expected_recipe_id, *, key=None):
    return await WeeklyMealLoggingService(AsyncUnitOfWork).log(
        LogMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=slot.id,
            idempotency_key=key or str(uuid4()),
            meal_date=WEEK + timedelta(days=slot.day_index),
            meal_type=("breakfast", "lunch", "dinner")[slot.slot_index],
            expected_recipe_id=expected_recipe_id,
            portion_multiplier=1.0,
            timezone="UTC",
        )
    )


async def _grocery_totals(user_id, plan_id):
    async with AsyncUnitOfWork() as uow:
        current = await uow.weekly_meal_plans.get_by_id(
            user_id=user_id, plan_id=plan_id
        )
        categories = await WeeklyGroceryService().calculate(uow, current)
    return {
        item.name: (item.ingredient_id, item.total_needed, item.unit)
        for category in categories
        for item in category.items
    }


@pytest.mark.asyncio
async def test_saved_recipe_slot_renders_logs_and_outlives_the_saved_recipe(
    pg_session, uow_on_test_db
):
    user_id, plan, _ = await _seed_with_saved_recipe(pg_session)

    updated = await _put_saved(user_id, plan)

    assert updated.revision == plan.revision + 1
    slot = _slot_at(updated, DINNER)
    assert (slot.recipe_id, slot.saved_suggestion_id) == (None, SAVED_ID)
    body = await _payload(await _current(user_id, plan.id))
    recipe = _payload_slot(body, DINNER)["recipe"]
    # The slot's own copy, so the dish shows its lines and steps even when it
    # is in no saved list; amounts cover the whole recipe (4 servings).
    copy = recipe.pop("saved_recipe")
    assert copy["suggestion_id"] == SAVED_ID
    assert copy["suggestion_data"]["servings"] == 4
    assert [
        (line["name"], line["grams"]) for line in copy["suggestion_data"]["ingredients"]
    ] == [("Rice noodles", 400.0), ("Chicken thigh", 600.0), ("Eggs", 200.0)]
    assert copy["suggestion_data"]["recipe_steps"] == [
        {"instruction": "Simmer the broth."}
    ]
    assert recipe == {
        "id": SAVED_ID,
        "name": "Grandma's chicken pho",
        "image_url": "https://images.example/pho.jpg",
        "cook_time_minutes": 60,
        "calories": 402,
        "nutrition_per_serving": {
            "calories": 402.0,
            "protein": 30.0,
            "carbs": 50.0,
            "fat": 10.0,
            "fiber": 4.0,
        },
        "is_vegetarian": False,
        "is_pork_free": False,
        "kind": "saved",
    }
    assert _payload_slot(body, (0, 0))["recipe"]["kind"] == "catalog"
    assert body["total_meals_planned"] == 21
    assert "saved_recipe_slots" in body["features"]

    # The app's promise: removing the saved recipe leaves this week as it is.
    deleted = await DeleteSavedSuggestionCommandHandler(
        uow=AsyncUnitOfWork(), event_publisher=_Publisher()
    ).handle(DeleteSavedSuggestionCommand(user_id=user_id, suggestion_id=SAVED_ID))
    assert deleted == {"success": True}
    after_delete = await _current(user_id, plan.id)
    assert _payload_slot(await _payload(after_delete), DINNER)["recipe"] == recipe

    logged = await _log(user_id, after_delete, slot, SAVED_ID, key="log-saved")
    replayed = await _log(user_id, after_delete, slot, SAVED_ID, key="log-saved")
    assert replayed == logged
    async with AsyncUnitOfWork() as uow:
        meal = await uow.meals.find_by_id(logged.logged_meal_id)
    assert meal.dish_name == "Grandma's chicken pho"
    assert meal.meal_type == "dinner" and meal.source == "weekly_meal_planner"
    macros = meal.nutrition.macros
    assert (macros.protein, macros.carbs, macros.fat, macros.fiber) == (
        30.0,
        50.0,
        10.0,
        4.0,
    )
    assert logged.calories == pytest.approx(402.0)
    assert meal.nutrition.calories == pytest.approx(402.0)
    # Ingredients per serving: the recipe's lines divided by its 4 servings.
    assert sorted(
        (item.name, item.quantity, item.unit) for item in meal.nutrition.food_items
    ) == [
        ("Chicken thigh", 150.0, "g"),
        ("Eggs", 1.0, "piece"),
        ("Rice noodles", 100.0, "g"),
    ]
    logged_slot = _slot_at(await _current(user_id, plan.id), DINNER)
    assert (logged_slot.is_logged, logged_slot.log_source) == (True, "plan")
    assert logged_slot.saved_suggestion_id == SAVED_ID

    # Unlog deletes the plan's diary meal and keeps the saved recipe planned.
    service = WeeklyMealSlotStateService(AsyncUnitOfWork, event_publisher=_Publisher())
    unlogged = await service.unlog(
        UnlogMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=slot.id,
            idempotency_key=str(uuid4()),
        )
    )
    released = _slot_at(unlogged, DINNER)
    assert not released.is_logged and released.saved_recipe is not None
    async with AsyncUnitOfWork() as uow:
        gone = await uow.session.execute(
            select(MealORM.meal_id).where(MealORM.meal_id == logged.logged_meal_id)
        )
    assert gone.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_saved_recipe_patch_errors_match_the_recipe_path(
    pg_session, uow_on_test_db
):
    user_id, plan, _ = await _seed_with_saved_recipe(pg_session)
    other_user = await _add_user(pg_session)
    await AsyncSavedSuggestionDbRepository(pg_session).save(
        user_id=other_user,
        suggestion_id="recipe_import_other",
        meal_type="dinner",
        portion_multiplier=1,
        suggestion_data=_import_payload(),
    )
    await pg_session.commit()

    for foreign_or_unknown in ("recipe_import_other", "recipe_import_missing"):
        with pytest.raises(ResourceNotFoundException) as exc:
            await WeeklyMealPlanService(AsyncUnitOfWork).update(
                UpdateWeeklyMealPlanCommand(
                    user_id=user_id,
                    plan_id=plan.id,
                    idempotency_key=str(uuid4()),
                    saved_slots={DINNER: foreign_or_unknown},
                )
            )
        assert exc.value.error_code == "SAVED_RECIPE_NOT_FOUND"

    with pytest.raises(WeeklyMealPlanConflictError) as stale:
        await _put_saved(user_id, plan, revision=plan.revision + 5)
    assert stale.value.error_code == "WEEKLY_PLAN_STALE_REVISION"

    await _log(user_id, plan, _slot_at(plan, (0, 0)), plan.slots[0].recipe_id)
    with pytest.raises(WeeklyMealPlanConflictError, match="Logged"):
        await _put_saved(user_id, plan, coordinate=(0, 0))

    # Nothing above changed the plan; the same key then replays one write.
    current = await _current(user_id, plan.id)
    assert current.revision == plan.revision
    first = await _put_saved(user_id, plan, key="same-key")
    again = await _put_saved(user_id, plan, key="same-key")
    assert first.revision == again.revision == plan.revision + 1


@pytest.mark.asyncio
async def test_groceries_skip_link_and_swap_back_with_a_saved_slot(
    pg_session, uow_on_test_db
):
    user_id, plan, rice_id = await _seed_with_saved_recipe(pg_session)
    before = await _grocery_totals(user_id, plan.id)

    await _put_saved(user_id, plan)
    with_saved = await _grocery_totals(user_id, plan.id)

    # The catalog dinner it replaced (100 g rice) is gone; the saved lines
    # are 1/4 of the recipe for the plan's one person.
    assert with_saved["Rice"][:2] == (rice_id, before["Rice"][1] - 100)
    assert with_saved["Rice noodles"][1:] == (100.0, "g")
    assert with_saved["Chicken thigh"][1:] == (150.0, "g")
    assert with_saved["Eggs"][1:] == (1.0, "piece")

    current = await _current(user_id, plan.id)
    dinner = _slot_at(current, DINNER)
    service = WeeklyMealSlotStateService(AsyncUnitOfWork)
    await service.set_skip(
        SetMealPlanSlotSkipCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=dinner.id,
            idempotency_key=str(uuid4()),
            skipped=True,
        )
    )
    skipped = await _grocery_totals(user_id, plan.id)
    assert "Chicken thigh" not in skipped and "Eggs" not in skipped

    async with AsyncUnitOfWork() as uow:
        diary_meal = (
            await RecommendedMealMaterializationService().materialize_from_saved_recipe(
                uow,
                user_id=user_id,
                recipe=dinner.saved_recipe,
                meal_date=WEEK + timedelta(days=1),
                meal_type="dinner",
                timezone="UTC",
                source="manual",
            )
        )
    linked = await service.link(
        LinkMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=dinner.id,
            idempotency_key=str(uuid4()),
            meal_id=diary_meal.meal_id,
        )
    )
    linked_slot = _slot_at(linked, DINNER)
    assert (linked_slot.log_source, linked_slot.is_skipped) == ("diary", False)
    assert linked_slot.saved_suggestion_id == SAVED_ID
    await service.unlog(
        UnlogMealPlanSlotCommand(
            user_id=user_id,
            plan_id=plan.id,
            slot_id=dinner.id,
            idempotency_key=str(uuid4()),
        )
    )

    # Back to a catalog recipe: the copy is dropped with the saved id.
    swapped = await WeeklyMealPlanService(AsyncUnitOfWork).update(
        UpdateWeeklyMealPlanCommand(
            user_id=user_id,
            plan_id=plan.id,
            idempotency_key=str(uuid4()),
            slots={DINNER: plan.slots[2].recipe_id},
        )
    )
    assert _slot_at(swapped, DINNER).recipe_id == plan.slots[2].recipe_id
    async with AsyncUnitOfWork() as uow:
        row = (
            await uow.session.execute(
                select(
                    WeeklyMealPlanSlotORM.saved_suggestion_id,
                    WeeklyMealPlanSlotORM.saved_recipe,
                ).where(WeeklyMealPlanSlotORM.id == dinner.id)
            )
        ).one()
    assert tuple(row) == (None, None)
    assert await _grocery_totals(user_id, plan.id) == before


@pytest.mark.asyncio
async def test_slot_table_refuses_two_recipes_or_a_saved_id_without_its_copy(
    pg_session, uow_on_test_db
):
    _, plan, _ = await _seed(pg_session)
    snapshot = SavedRecipeSnapshot.from_saved_suggestion(SAVED_ID, _import_payload())
    breakfast = plan.slots[0]

    for assignment in (
        "saved_suggestion_id = :saved, saved_recipe = CAST(:copy AS JSONB)",
        "catalog_meal_id = NULL, saved_suggestion_id = :saved",
    ):
        with pytest.raises(IntegrityError):
            await pg_session.execute(
                text(f"UPDATE weekly_meal_plan_slots SET {assignment} WHERE id = :id"),
                {
                    "saved": SAVED_ID,
                    "copy": json.dumps(snapshot.to_dict()),
                    "id": breakfast.id,
                },
            )
        await pg_session.rollback()

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.unit.infra.repositories.test_meal_recommendation_plan_repository_async import (
    _plan,
)

from src.app.commands.meal_recommendation import (
    CreateThreeDayMealRecommendationCommand,
    LogRecommendedMealCommand,
    SkipMealRecommendationSlotCommand,
)
from src.app.handlers.command_handlers.meal_recommendation.create_three_day_meal_recommendation_command_handler import (
    CreateThreeDayMealRecommendationCommandHandler,
    _request_fingerprint,
)
from src.app.handlers.command_handlers.meal_recommendation.log_recommended_meal_command_handler import (
    LogRecommendedMealCommandHandler,
)
from src.app.handlers.command_handlers.meal_recommendation.skip_meal_recommendation_slot_command_handler import (
    SkipMealRecommendationSlotCommandHandler,
)
from src.app.handlers.query_handlers.get_meal_recommendation_plan_query_handler import (
    GetMealRecommendationPlanQueryHandler,
)
from src.app.queries.meal_recommendation import GetMealRecommendationPlanQuery
from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationCreationError,
    MealRecommendationPersistenceConflictError,
)
from src.domain.model.meal_recommendation import (
    CatalogMeal,
    MealRecommendationAlternative,
    MealRecommendationPlan,
    MealRecommendationSlot,
    PersistedMealRecommendationSlotMutationResult,
)
from src.domain.services.meal_recommendation.ingredient_affinity_service import (
    IngredientAffinityProfile,
)
from src.infra.event_bus.background_task_manager import BackgroundTaskManager


class _PlanRepo:
    def __init__(self):
        self.existing = None
        self.lock_generation_for_user = AsyncMock()
        self.get_by_idempotency_key = AsyncMock(side_effect=self._get_by_key)
        self.get_by_id = AsyncMock(return_value=_plan())
        self.get_summary = AsyncMock(return_value=_plan())
        self.mark_shown = AsyncMock()
        self.save_new_active_plan = AsyncMock(side_effect=lambda plan: plan)

    async def _get_by_key(self, **kwargs):
        return self.existing


class _CatalogRepo:
    def __init__(self, meals=None):
        self.meals = meals or []

    async def list_active_meals(self):
        return self.meals


class _ConflictPlanRepo(_PlanRepo):
    def __init__(self, replay_plan):
        super().__init__()
        self.existing = replay_plan
        self._reads = 0
        self.save_new_active_plan = AsyncMock(
            side_effect=MealRecommendationPersistenceConflictError
        )

    async def _get_by_key(self, **kwargs):
        self._reads += 1
        return None if self._reads == 1 else self.existing


class _LogPlanRepo(_PlanRepo):
    def __init__(self, *, replayed=False):
        super().__init__()
        self.claim_slot_log = AsyncMock(
            return_value=(_plan(), _plan().slots[0], replayed)
        )
        self.finalize_slot_logged = AsyncMock(
            return_value=PersistedMealRecommendationSlotMutationResult(
                plan_id="plan-1",
                user_id="user-1",
                slot=_plan().slots[0],
            )
        )


class _SkipPlanRepo(_PlanRepo):
    def __init__(self):
        super().__init__()
        self.skip_slot = AsyncMock(
            return_value=PersistedMealRecommendationSlotMutationResult(
                plan_id="plan-1",
                user_id="user-1",
                slot=_plan().slots[0],
            )
        )


class _Materializer:
    def __init__(self, meal=None):
        food_item = type(
            "FoodItem",
            (),
            {"id": "food-1", "name": "Rice"},
        )()
        nutrition = type("Nutrition", (), {"food_items": [food_item]})()
        self.meal = (
            meal
            or type(
                "Meal",
                (),
                {
                    "meal_id": "meal-1",
                    "dish_name": "Rice Bowl",
                    "nutrition": nutrition,
                },
            )()
        )
        self.materialize = AsyncMock(return_value=self.meal)


class _Uow:
    def __init__(self, plans, catalog):
        self.meal_recommendation_plans = plans
        self.catalog_recipes = catalog

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class _Optimizer:
    def build_plan(self, catalog_meals, *, daily_calories, affinity, **kwargs):
        recipe = _catalog_meal("catalog-1")
        alternative = _catalog_meal("catalog-2")
        return MealRecommendationPlan(
            slots=(
                MealRecommendationSlot(
                    day_index=0,
                    meal_type="breakfast",
                    target_calories=500,
                    catalog_meal=recipe,
                    score=1.0,
                ),
            ),
            alternatives={
                (0, "breakfast"): (
                    MealRecommendationAlternative(
                        day_index=0,
                        meal_type="breakfast",
                        target_calories=500,
                        catalog_meal=alternative,
                        score=0.9,
                    ),
                )
            },
        )


class _HistoryProjector:
    async def build_affinity(self, uow, *, user_id, start_date, timezone):
        return IngredientAffinityProfile(weights={}, confidence=0.0)


def _command() -> CreateThreeDayMealRecommendationCommand:
    return CreateThreeDayMealRecommendationCommand(
        user_id="user-1",
        idempotency_key="key-1",
        start_date=date(2026, 7, 16),
        timezone="UTC",
        daily_calories=2000,
    )


def _catalog_meal(meal_id: str) -> CatalogMeal:
    return CatalogMeal(
        id=meal_id,
        catalog_key=f"key-{meal_id}",
        content_hash=f"{meal_id:0<64}"[:64],
        name=f"Meal {meal_id}",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("125"),
        carbs_g=Decimal("0"),
        fat_g=Decimal("0"),
        fiber_g=Decimal("0"),
        meal_types=("breakfast",),
        is_active=True,
    )


def _log_command(*, language: str = "en") -> LogRecommendedMealCommand:
    return LogRecommendedMealCommand(
        user_id="user-1",
        plan_id="plan-1",
        slot_id="slot-1",
        request_id="log-1",
        language=language,
    )


@pytest.mark.asyncio
async def test_create_handler_replays_existing_idempotent_plan():
    plans = _PlanRepo()
    plan = _plan()
    plans.existing = plan.__class__(
        **{**plan.__dict__, "request_fingerprint": _request_fingerprint(_command())}
    )
    handler = CreateThreeDayMealRecommendationCommandHandler(
        uow=_Uow(plans, _CatalogRepo()),
        history_projector=_HistoryProjector(),
    )

    result = await handler.handle(_command())

    assert result.id == "plan-1"
    plans.save_new_active_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_handler_conflicts_on_idempotency_fingerprint_mismatch():
    plans = _PlanRepo()
    plans.existing = _plan()
    handler = CreateThreeDayMealRecommendationCommandHandler(
        uow=_Uow(plans, _CatalogRepo()),
        history_projector=_HistoryProjector(),
    )

    with pytest.raises(MealRecommendationCreationError, match="Idempotency-Key"):
        await handler.handle(_command())

    plans.save_new_active_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_handler_fails_closed_without_catalog_meals():
    handler = CreateThreeDayMealRecommendationCommandHandler(
        uow=_Uow(_PlanRepo(), _CatalogRepo(meals=[])),
        history_projector=_HistoryProjector(),
    )

    with pytest.raises(MealRecommendationCreationError, match="catalog is unavailable"):
        await handler.handle(_command())


@pytest.mark.asyncio
async def test_create_handler_replays_after_persistence_conflict():
    plan = _plan()
    replay_plan = plan.__class__(
        **{**plan.__dict__, "request_fingerprint": _request_fingerprint(_command())}
    )
    plans = _ConflictPlanRepo(replay_plan)
    handler = CreateThreeDayMealRecommendationCommandHandler(
        uow=_Uow(plans, _CatalogRepo(meals=[_catalog_meal("catalog-1")])),
        optimizer=_Optimizer(),
        history_projector=_HistoryProjector(),
    )

    result = await handler.handle(_command())

    assert result.id == "plan-1"
    assert plans.get_by_idempotency_key.await_count == 2


@pytest.mark.asyncio
async def test_query_handler_reads_owner_scoped_plan():
    plans = _PlanRepo()
    handler = GetMealRecommendationPlanQueryHandler(
        uow_factory=lambda: _Uow(plans, _CatalogRepo())
    )

    result = await handler.handle(
        GetMealRecommendationPlanQuery(user_id="user-1", plan_id="plan-1")
    )

    assert result is not None
    assert result.id == "plan-1"
    plans.get_summary.assert_awaited_once_with(user_id="user-1", plan_id="plan-1")
    plans.mark_shown.assert_awaited_once_with(
        user_id="user-1",
        plan_id="plan-1",
        slot_ids=("slot-1",),
    )


@pytest.mark.asyncio
async def test_log_handler_replays_without_materializing_duplicate_meal():
    plans = _LogPlanRepo(replayed=True)
    materializer = _Materializer()
    handler = LogRecommendedMealCommandHandler(
        uow=_Uow(plans, _CatalogRepo()),
        materializer=materializer,
    )

    result = await handler.handle(_log_command())

    assert result.plan_id == "plan-1"
    plans.claim_slot_log.assert_awaited_once()
    materializer.materialize.assert_not_awaited()
    plans.finalize_slot_logged.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_handler_claims_materializes_then_finalizes():
    plans = _LogPlanRepo(replayed=False)
    materializer = _Materializer()
    handler = LogRecommendedMealCommandHandler(
        uow=_Uow(plans, _CatalogRepo()),
        materializer=materializer,
    )

    result = await handler.handle(_log_command())

    assert result.plan_id == "plan-1"
    plans.claim_slot_log.assert_awaited_once()
    materializer.materialize.assert_awaited_once()
    plans.finalize_slot_logged.assert_awaited_once_with(
        user_id="user-1",
        plan_id="plan-1",
        slot_id="slot-1",
        request_id="log-1",
        meal_id="meal-1",
    )


@pytest.mark.asyncio
async def test_log_handler_translates_and_invalidates_cache_for_non_english(caplog):
    caplog.set_level("INFO")
    plans = _LogPlanRepo(replayed=False)
    materializer = _Materializer()
    translation_service = type(
        "TranslationService",
        (),
        {"translate_meal": AsyncMock(return_value={"dish_name": "Rice Bowl"})},
    )()
    event_publisher = MagicMock()
    event_publisher.publish = AsyncMock()
    handler = LogRecommendedMealCommandHandler(
        uow=_Uow(plans, _CatalogRepo()),
        materializer=materializer,
        meal_translation_service=translation_service,
        event_publisher=event_publisher,
        task_manager=BackgroundTaskManager(),
    )

    await handler.handle(_log_command(language="vi"))
    await handler.task_manager.drain()

    translation_service.translate_meal.assert_awaited_once()
    kwargs = translation_service.translate_meal.await_args.kwargs
    assert kwargs["target_language"] == "vi"
    assert kwargs["dish_name"] == "Rice Bowl"
    assert kwargs["food_items"][0].name == "Rice"
    event_publisher.publish.assert_awaited_once()
    assert "recommended meal translated" not in caplog.text


@pytest.mark.asyncio
async def test_log_handler_skips_translation_for_english():
    plans = _LogPlanRepo(replayed=False)
    materializer = _Materializer()
    translation_service = type(
        "TranslationService",
        (),
        {"translate_meal": AsyncMock(return_value=None)},
    )()
    handler = LogRecommendedMealCommandHandler(
        uow=_Uow(plans, _CatalogRepo()),
        materializer=materializer,
        meal_translation_service=translation_service,
    )

    await handler.handle(_log_command(language="en"))

    translation_service.translate_meal.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_handler_does_not_fail_when_translation_raises():
    plans = _LogPlanRepo(replayed=False)
    materializer = _Materializer()
    translation_service = type(
        "TranslationService",
        (),
        {"translate_meal": AsyncMock(side_effect=RuntimeError("provider down"))},
    )()
    handler = LogRecommendedMealCommandHandler(
        uow=_Uow(plans, _CatalogRepo()),
        materializer=materializer,
        meal_translation_service=translation_service,
        task_manager=BackgroundTaskManager(),
    )

    result = await handler.handle(_log_command(language="vi"))
    await handler.task_manager.drain()

    assert result.plan_id == "plan-1"
    translation_service.translate_meal.assert_awaited_once()


@pytest.mark.asyncio
async def test_skip_handler_skips_slot_without_materializing_meal():
    plans = _SkipPlanRepo()
    handler = SkipMealRecommendationSlotCommandHandler(uow=_Uow(plans, _CatalogRepo()))

    result = await handler.handle(
        SkipMealRecommendationSlotCommand(
            user_id="user-1",
            plan_id="plan-1",
            slot_id="slot-1",
            request_id="skip-1",
        )
    )

    assert result.plan_id == "plan-1"
    plans.skip_slot.assert_awaited_once_with(
        user_id="user-1",
        plan_id="plan-1",
        slot_id="slot-1",
        request_id="skip-1",
    )

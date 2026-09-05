import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from tests.unit.infra.repositories.test_meal_recommendation_plan_repository_async import (
    _plan,
)

from src.api.routes.v1.meal_recommendation_route_support import to_response
from src.api.routes.v1.meal_recommendations import (
    LogRecommendedMealRequest,
    SkipMealRecommendationSlotRequest,
    SwapMealRecommendationSlotRequest,
    create_three_day_recommendations,
    get_meal_recommendation_plan,
    get_meal_recommendation_slot_detail,
    log_recommended_meal,
    skip_meal_recommendation_slot,
    swap_meal_recommendation_slot,
)
from src.app.commands.meal_recommendation import (
    CreateThreeDayMealRecommendationCommand,
    LogRecommendedMealCommand,
    SkipMealRecommendationSlotCommand,
    SwapMealRecommendationSlotCommand,
)
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.app.queries.meal_recommendation import (
    GetMealRecommendationPlanQuery,
    GetMealRecommendationSlotDetailQuery,
)
from src.app.queries.user import GetUserTimezoneQuery
from src.domain.model.meal_recommendation import (
    PersistedMealRecommendationSlotMutationResult,
)


class _EventBus:
    def __init__(self):
        self.commands = []

    async def send(self, message):
        self.commands.append(message)
        if isinstance(message, GetUserTimezoneQuery):
            return "Asia/Ho_Chi_Minh"
        if isinstance(message, GetWeeklyBudgetQuery):
            return {"adjusted_daily_calories": 2150}
        if isinstance(message, GetMealRecommendationPlanQuery):
            return _plan()
        if isinstance(message, GetMealRecommendationSlotDetailQuery):
            return _plan().slots[0]
        if isinstance(
            message,
            (
                SwapMealRecommendationSlotCommand,
                LogRecommendedMealCommand,
                SkipMealRecommendationSlotCommand,
            ),
        ):
            return PersistedMealRecommendationSlotMutationResult(
                plan_id="plan-1",
                user_id="user-1",
                slot=_plan().slots[0],
            )
        return _plan()


class _Analytics:
    def __init__(self):
        self.events = []

    async def capture_plan_response(self, *, user_id, event, plan):
        self.events.append((user_id, event, plan.id))

    async def capture_slot_response(self, **kwargs):
        self.events.append(kwargs)


class _TaskManager:
    def __init__(self):
        self.tasks = []

    def spawn(self, name, coro):
        self.tasks.append((name, coro))
        coro.close()


class _Translator:
    def __init__(self) -> None:
        self.calls = []

    async def translate_texts(self, texts, target_lang):
        self.calls.append((texts, target_lang))
        return [f"vi:{text}" for text in texts]


def _request(
    timezone: str = "Asia/Ho_Chi_Minh",
    language: str = "en",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/meal-recommendations/three-day",
            "client": ("127.0.0.1", 12345),
            "headers": [(b"x-timezone", timezone.encode("utf-8"))],
            "state": {"language": language},
        }
    )


def test_meal_recommendation_response_includes_allergy_not_evaluated_and_slots():
    response = to_response(_plan())

    assert response.id == "plan-1"
    assert response.allergy_evaluated is False
    assert response.slots[0].catalog_meal_id == "catalog-1"
    assert response.slots[0].catalog_meal.name == "Breakfast Rice"
    assert response.slots[0].catalog_meal.calories == 380
    assert response.slots[0].catalog_meal.ingredients[0].display_name == "Rice"
    assert response.slots[0].alternatives[0].catalog_meal_id == "catalog-2"
    assert response.slots[0].alternatives[0].catalog_meal.name == "Chicken Bowl"


def test_swap_request_rejects_unsupported_reason():
    with pytest.raises(ValidationError):
        SwapMealRecommendationSlotRequest(
            request_id="swap-1",
            expected_selection_version=1,
            reason="unsupported",
        )


def test_log_request_rejects_blank_request_id_after_trim():
    with pytest.raises(ValidationError):
        LogRecommendedMealRequest(request_id="   ")


def test_skip_request_rejects_blank_request_id_after_trim():
    with pytest.raises(ValidationError):
        SkipMealRecommendationSlotRequest(request_id="   ")


@pytest.mark.asyncio
async def test_create_three_day_recommendations_rejects_blank_idempotency_key():
    with pytest.raises(HTTPException) as exc_info:
        await create_three_day_recommendations(
            request=_request(),
            idempotency_key="   ",
            user_id="user-1",
            analytics_service=_Analytics(),
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_three_day_recommendations_snapshots_target_and_timezone():
    event_bus = _EventBus()
    analytics = _Analytics()

    response = await create_three_day_recommendations(
        request=_request(),
        idempotency_key=" key-1 ",
        user_id="user-1",
        event_bus=event_bus,
        analytics_service=analytics,
    )

    command = next(
        item
        for item in event_bus.commands
        if isinstance(item, CreateThreeDayMealRecommendationCommand)
    )
    assert response.id == "plan-1"
    assert not hasattr(response.slots[0], "alternatives")
    assert response.slots[0].catalog_meal.ingredients[0].display_name == "Rice"
    assert command.idempotency_key == "key-1"
    assert command.timezone == "Asia/Ho_Chi_Minh"
    assert command.daily_calories == 2150
    assert [item[1] for item in analytics.events] == [
        "plan_shown",
        "alternatives_shown",
    ]


@pytest.mark.asyncio
async def test_create_three_day_recommendations_captures_analytics():
    event_bus = _EventBus()
    analytics = _Analytics()

    response = await create_three_day_recommendations(
        request=_request(),
        idempotency_key="key-1",
        user_id="user-1",
        event_bus=event_bus,
        analytics_service=analytics,
    )

    assert response.id == "plan-1"
    assert [item[1] for item in analytics.events] == [
        "plan_shown",
        "alternatives_shown",
    ]


@pytest.mark.asyncio
async def test_get_plan_returns_owner_scoped_compact_summary():
    response = await get_meal_recommendation_plan(
        plan_id="plan-1",
        request=_request(),
        user_id="user-1",
        event_bus=_EventBus(),
        analytics_service=_Analytics(),
    )

    assert response.id == "plan-1"
    assert response.slots[0].catalog_meal.name == "Breakfast Rice"
    assert response.slots[0].catalog_meal.calories == 380
    assert not hasattr(response.slots[0], "alternatives")
    assert response.slots[0].catalog_meal.ingredients[0].display_name == "Rice"


@pytest.mark.asyncio
async def test_get_slot_detail_returns_one_hydrated_slot_with_alternatives():
    response = await get_meal_recommendation_slot_detail(
        plan_id="plan-1",
        slot_id="slot-1",
        request=_request(),
        user_id="user-1",
        event_bus=_EventBus(),
        analytics_service=_Analytics(),
    )

    assert response.plan_id == "plan-1"
    assert response.slot.id == "slot-1"
    assert response.slot.catalog_meal.ingredients[0].display_name == "Rice"
    assert response.slot.alternatives[0].catalog_meal.name == "Chicken Bowl"


@pytest.mark.asyncio
async def test_get_plan_translates_catalog_text_from_request_language():
    translator = _Translator()

    response = await get_meal_recommendation_plan(
        plan_id="plan-1",
        request=_request(language="vi"),
        user_id="user-1",
        event_bus=_EventBus(),
        analytics_service=_Analytics(),
        translation_service=translator,
    )

    assert response.slots[0].catalog_meal.name == "vi:Breakfast Rice"
    assert response.slots[0].catalog_meal.cuisine == "vi:vietnamese"
    assert response.slots[0].catalog_meal.calories == 380
    assert response.slots[0].catalog_meal.ingredients[0].display_name == "vi:Rice"
    assert translator.calls == [
        (
            [
                "Breakfast Rice",
                "vietnamese",
                "Display copy",
                "Rice",
                "Chicken Bowl",
            ],
            "vi",
        )
    ]


@pytest.mark.asyncio
async def test_swap_route_sends_expected_selection_version_command():
    event_bus = _EventBus()

    response = await swap_meal_recommendation_slot(
        request=_request(),
        plan_id="plan-1",
        slot_id="slot-1",
        body=SwapMealRecommendationSlotRequest(
            request_id="swap-1",
            expected_selection_version=1,
            alternative_catalog_meal_id="selection_version-2",
        ),
        user_id="user-1",
        event_bus=event_bus,
        analytics_service=_Analytics(),
    )

    command = next(
        item
        for item in event_bus.commands
        if isinstance(item, SwapMealRecommendationSlotCommand)
    )
    assert command.expected_selection_version == 1
    assert command.alternative_catalog_meal_id == "selection_version-2"
    assert response.plan_id == "plan-1"
    assert response.slot.id == "slot-1"


@pytest.mark.asyncio
async def test_log_route_sends_recommended_meal_command():
    event_bus = _EventBus()

    response = await log_recommended_meal(
        request=_request(),
        plan_id="plan-1",
        slot_id="slot-1",
        body=LogRecommendedMealRequest(request_id="log-1"),
        user_id="user-1",
        event_bus=event_bus,
        analytics_service=_Analytics(),
    )

    command = next(
        item for item in event_bus.commands if isinstance(item, LogRecommendedMealCommand)
    )
    assert command.request_id == "log-1"
    assert command.language == "en"
    assert response.plan_id == "plan-1"
    assert response.slot.id == "slot-1"


@pytest.mark.asyncio
async def test_log_route_forwards_request_language_to_command():
    event_bus = _EventBus()

    await log_recommended_meal(
        request=_request(language="vi"),
        plan_id="plan-1",
        slot_id="slot-1",
        body=LogRecommendedMealRequest(request_id="log-1"),
        user_id="user-1",
        event_bus=event_bus,
        analytics_service=_Analytics(),
    )

    command = next(
        item for item in event_bus.commands if isinstance(item, LogRecommendedMealCommand)
    )
    assert command.language == "vi"


@pytest.mark.asyncio
async def test_skip_route_sends_skip_slot_command():
    event_bus = _EventBus()

    response = await skip_meal_recommendation_slot(
        request=_request(),
        plan_id="plan-1",
        slot_id="slot-1",
        body=SkipMealRecommendationSlotRequest(request_id="skip-1"),
        user_id="user-1",
        event_bus=event_bus,
        analytics_service=_Analytics(),
    )

    command = next(
        item for item in event_bus.commands if isinstance(item, SkipMealRecommendationSlotCommand)
    )
    assert command.request_id == "skip-1"
    assert response.plan_id == "plan-1"
    assert response.slot.id == "slot-1"


def test_mutation_routes_have_abuse_limits():
    for route in (
        swap_meal_recommendation_slot,
        log_recommended_meal,
        skip_meal_recommendation_slot,
    ):
        assert getattr(route, "__wrapped__", None) is not None

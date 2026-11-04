"""Unlog, link and skip for weekly plan slots, against in-memory fakes."""

from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from src.api.exceptions import (
    ConflictException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal_planner import (
    LinkMealPlanSlotCommand,
    SetMealPlanSlotSkipCommand,
    UnlogMealPlanSlotCommand,
)
from src.app.events.meal.meal_events import register_local_cache_invalidation_hook
from src.app.services.weekly_meal_slot_state_service import (
    WeeklyMealSlotStateService,
)
from src.domain.model.meal.meal import MealStatus
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)

WEEK = date(2026, 9, 21)
USER = "user-1"


def _plan(**slot_overrides):
    slots = []
    for day in range(7):
        for index in range(3):
            slot = WeeklyMealPlanSlot(
                id=f"slot-{day}-{index}",
                day_index=day,
                slot_index=index,
                recipe_id=f"recipe-{index}",
            )
            slots.append(replace(slot, **slot_overrides.get(slot.id, {})))
    return WeeklyMealPlan(
        id="plan-1",
        user_id=USER,
        week_start_date=WEEK,
        status=WeeklyMealPlanStatus.DRAFT,
        people=1,
        preferences=WeeklyMealPlanPreferences(),
        timezone="UTC",
        slots=tuple(slots),
    )


class _Plans:
    def __init__(self, plan):
        self.plan = plan
        self.cleared_meals = []
        self.updates = []

    async def get_plan_for_slot_write(self, *, user_id, plan_id):
        if user_id != self.plan.user_id or plan_id != self.plan.id:
            return None
        return self.plan

    async def get_by_id(self, *, user_id, plan_id):
        return await self.get_plan_for_slot_write(user_id=user_id, plan_id=plan_id)

    async def update_slot_state(self, *, user_id, plan_id, slot_id, **changes):
        self.updates.append((slot_id, changes))
        self.plan = replace(
            self.plan,
            slots=tuple(
                replace(slot, version=slot.version + 1, **changes)
                if slot.id == slot_id
                else slot
                for slot in self.plan.slots
            ),
        )
        return self.plan

    async def find_slot_id_for_logged_meal(self, *, user_id, meal_id):
        return next(
            (slot.id for slot in self.plan.slots if slot.logged_meal_id == meal_id),
            None,
        )

    async def clear_links_for_deleted_meal(self, *, meal_id):
        self.cleared_meals.append(meal_id)


class _Meals:
    def __init__(self, *meals):
        self.meals = {meal.meal_id: meal for meal in meals}
        self.deleted = []

    async def find_by_id(self, meal_id):
        return self.meals.get(meal_id)

    async def delete(self, meal_id):
        self.deleted.append(meal_id)
        self.meals.pop(meal_id, None)


class _Operations:
    def __init__(self, state="acquired"):
        self.state = state
        self.reserved = []
        self.completed = []
        self.released = []

    async def reserve(self, **kwargs):
        self.reserved.append(kwargs)
        return SimpleNamespace(state=self.state, target_meal_id="plan-1")

    async def complete(self, reservation, **kwargs):
        self.completed.append(kwargs)

    async def release(self, reservation):
        self.released.append(reservation)


class _Users:
    def __init__(self, timezone="UTC"):
        self.timezone = timezone

    async def get_user_timezone(self, user_id):
        return self.timezone


def _meal(meal_id, *, created_at, meal_type="lunch", user_id=USER, status=None):
    return SimpleNamespace(
        meal_id=meal_id,
        user_id=user_id,
        created_at=created_at,
        meal_type=meal_type,
        status=status or MealStatus.READY,
    )


def _service(plan, meals=(), *, operations=None, timezone="UTC", publisher=None):
    uow = SimpleNamespace(
        weekly_meal_plans=_Plans(plan),
        meals=_Meals(*meals),
        meal_write_operations=operations or _Operations(),
        users=_Users(timezone),
    )

    class _Factory:
        async def __aenter__(self):
            return uow

        async def __aexit__(self, *exc):
            return False

    return (
        WeeklyMealSlotStateService(
            _Factory, event_publisher=publisher, environment="test"
        ),
        uow,
    )


class _Publisher:
    def __init__(self):
        self.payloads = []

    async def publish(self, payload):
        self.payloads.append(payload)


@pytest.fixture
def purges():
    calls = []

    async def hook(user_id, meal_date, old_meal_date):
        calls.append((user_id, meal_date, old_meal_date))

    register_local_cache_invalidation_hook(hook)
    yield calls
    register_local_cache_invalidation_hook(None)


def _unlog(slot_id="slot-0-1"):
    return UnlogMealPlanSlotCommand(
        user_id=USER, plan_id="plan-1", slot_id=slot_id, idempotency_key="k"
    )


def _link(meal_id="meal-1", slot_id="slot-0-1", timezone=None):
    return LinkMealPlanSlotCommand(
        user_id=USER,
        plan_id="plan-1",
        slot_id=slot_id,
        idempotency_key="k",
        meal_id=meal_id,
        timezone=timezone,
    )


def _skip(skipped=True, slot_id="slot-0-1"):
    return SetMealPlanSlotSkipCommand(
        user_id=USER,
        plan_id="plan-1",
        slot_id=slot_id,
        idempotency_key="k",
        skipped=skipped,
    )


@pytest.mark.asyncio
async def test_unlog_plan_logged_slot_deletes_meal_publishes_and_purges(purges):
    lunch_time = datetime(2026, 9, 21, 12, tzinfo=UTC)
    plan = _plan(
        **{
            "slot-0-1": {
                "is_logged": True,
                "logged_meal_id": "meal-1",
                "log_source": "plan",
            }
        }
    )
    publisher = _Publisher()
    service, uow = _service(
        plan, [_meal("meal-1", created_at=lunch_time)], publisher=publisher
    )

    result = await service.unlog(_unlog())

    slot = next(slot for slot in result.slots if slot.id == "slot-0-1")
    assert (slot.is_logged, slot.logged_meal_id, slot.log_source) == (
        False,
        None,
        None,
    )
    assert uow.meals.deleted == ["meal-1"]
    # The meal delete path ran, so it also cleared any other link.
    assert uow.weekly_meal_plans.cleared_meals == ["meal-1"]
    assert [payload["event_type"] for payload in publisher.payloads] == [
        "meal.deleted.v1"
    ]
    assert purges == [(USER, date(2026, 9, 21), None)]
    assert uow.meal_write_operations.reserved[0]["operation"] == (
        "weekly_meal_plan_slot_unlog"
    )
    assert uow.meal_write_operations.completed == [
        {"target_meal_id": "plan-1", "response": {"plan_id": "plan-1"}}
    ]


@pytest.mark.asyncio
async def test_unlog_slot_logged_before_log_source_existed_deletes_meal(purges):
    plan = _plan(**{"slot-0-1": {"is_logged": True, "logged_meal_id": "meal-1"}})
    service, uow = _service(
        plan, [_meal("meal-1", created_at=datetime(2026, 9, 21, 12, tzinfo=UTC))]
    )

    await service.unlog(_unlog())

    assert uow.meals.deleted == ["meal-1"]


@pytest.mark.asyncio
async def test_unlog_linked_slot_keeps_the_diary_meal(purges):
    plan = _plan(
        **{
            "slot-0-1": {
                "is_logged": True,
                "logged_meal_id": "meal-1",
                "log_source": "diary",
            }
        }
    )
    publisher = _Publisher()
    service, uow = _service(
        plan,
        [_meal("meal-1", created_at=datetime(2026, 9, 21, 12, tzinfo=UTC))],
        publisher=publisher,
    )

    result = await service.unlog(_unlog())

    assert not next(slot for slot in result.slots if slot.id == "slot-0-1").is_logged
    assert uow.meals.deleted == []
    assert publisher.payloads == [] and purges == []


@pytest.mark.asyncio
async def test_unlog_stuck_slot_without_meal_only_releases_it(purges):
    plan = _plan(**{"slot-0-1": {"is_logged": True, "log_source": "plan"}})
    service, uow = _service(plan)

    result = await service.unlog(_unlog())

    assert not next(slot for slot in result.slots if slot.id == "slot-0-1").is_logged
    assert uow.meals.deleted == []


@pytest.mark.asyncio
async def test_unlog_rejects_a_slot_that_is_not_logged():
    service, uow = _service(_plan())

    with pytest.raises(ConflictException) as error:
        await service.unlog(_unlog())

    assert error.value.error_code == "WEEKLY_SLOT_NOT_LOGGED"
    assert uow.meal_write_operations.released


@pytest.mark.asyncio
async def test_unknown_plan_and_slot_are_404():
    service, _ = _service(_plan())

    with pytest.raises(ResourceNotFoundException) as missing_slot:
        await service.unlog(_unlog(slot_id="nope"))
    other_plan = UnlogMealPlanSlotCommand(
        user_id="someone-else",
        plan_id="plan-1",
        slot_id="slot-0-1",
        idempotency_key="k",
    )
    with pytest.raises(ResourceNotFoundException) as missing_plan:
        await service.unlog(other_plan)

    assert missing_slot.value.error_code == "WEEKLY_SLOT_NOT_FOUND"
    assert missing_plan.value.error_code == "WEEKLY_PLAN_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "error_code"),
    [
        ("fingerprint_conflict", "IDEMPOTENCY_KEY_REUSED"),
        ("in_progress", "IDEMPOTENCY_IN_PROGRESS"),
    ],
)
async def test_reservation_conflicts_are_409(state, error_code):
    service, _ = _service(_plan(), operations=_Operations(state))

    with pytest.raises(ConflictException) as error:
        await service.set_skip(_skip())

    assert error.value.error_code == error_code


@pytest.mark.asyncio
async def test_replay_returns_the_plan_without_writing():
    plan = _plan(**{"slot-0-1": {"is_logged": True, "logged_meal_id": "meal-1"}})
    service, uow = _service(plan, operations=_Operations("replay"))

    result = await service.unlog(_unlog())

    assert result is plan
    assert uow.weekly_meal_plans.updates == [] and uow.meals.deleted == []


@pytest.mark.asyncio
async def test_link_marks_slot_logged_from_diary_and_clears_skip():
    plan = _plan(**{"slot-0-1": {"is_skipped": True}})
    meal = _meal("meal-1", created_at=datetime(2026, 9, 21, 12, tzinfo=UTC))
    service, uow = _service(plan, [meal])

    result = await service.link(_link())

    slot = next(slot for slot in result.slots if slot.id == "slot-0-1")
    assert slot.is_logged and slot.logged_meal_id == "meal-1"
    assert slot.log_source == "diary" and slot.is_skipped is False
    assert uow.meals.deleted == []
    assert uow.meal_write_operations.reserved[0]["operation"] == (
        "weekly_meal_plan_slot_link"
    )


@pytest.mark.asyncio
async def test_link_uses_the_users_day_not_utc():
    # 18:00 UTC on Sunday is Monday 01:00 in Ho Chi Minh City.
    meal = _meal(
        "meal-1",
        created_at=datetime(2026, 9, 20, 18, tzinfo=UTC),
        meal_type="breakfast",
    )
    service, _ = _service(_plan(), [meal], timezone="Asia/Ho_Chi_Minh")

    result = await service.link(_link(slot_id="slot-0-0"))

    assert next(slot for slot in result.slots if slot.id == "slot-0-0").is_logged
    service, _ = _service(_plan(), [meal], timezone="UTC")
    with pytest.raises(ValidationException):
        await service.link(_link(slot_id="slot-0-0"))
    # A valid X-Timezone header wins over the saved timezone.
    service, _ = _service(_plan(), [meal], timezone="UTC")
    await service.link(_link(slot_id="slot-0-0", timezone="Asia/Ho_Chi_Minh"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meal",
    [
        _meal("meal-1", created_at=datetime(2026, 9, 22, 12, tzinfo=UTC)),
        _meal(
            "meal-1",
            created_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
            meal_type="dinner",
        ),
        _meal("meal-1", created_at=datetime(2026, 9, 21, 12), meal_type=None),
    ],
    ids=["other-day", "other-meal-type", "no-meal-type"],
)
async def test_link_rejects_a_meal_from_another_slot(meal):
    service, _ = _service(_plan(), [meal])

    with pytest.raises(ValidationException) as error:
        await service.link(_link())

    assert error.value.error_code == "WEEKLY_SLOT_MISMATCH"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "meal",
    [
        None,
        _meal(
            "meal-1",
            created_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
            user_id="someone-else",
        ),
        _meal(
            "meal-1",
            created_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
            status=MealStatus.INACTIVE,
        ),
    ],
    ids=["missing", "not-owned", "deleted"],
)
async def test_link_hides_missing_foreign_and_deleted_meals(meal):
    service, _ = _service(_plan(), [meal] if meal else [])

    with pytest.raises(ResourceNotFoundException) as error:
        await service.link(_link())

    assert error.value.error_code == "MEAL_NOT_FOUND"


@pytest.mark.asyncio
async def test_link_rejects_logged_slot_and_meal_linked_elsewhere():
    meal = _meal("meal-1", created_at=datetime(2026, 9, 21, 12, tzinfo=UTC))
    logged = _plan(**{"slot-0-1": {"is_logged": True, "logged_meal_id": "other"}})
    service, _ = _service(logged, [meal])
    with pytest.raises(ConflictException) as already_logged:
        await service.link(_link())

    linked_elsewhere = _plan(
        **{"slot-0-2": {"is_logged": True, "logged_meal_id": "meal-1"}}
    )
    service, _ = _service(linked_elsewhere, [meal])
    with pytest.raises(ConflictException) as already_linked:
        await service.link(_link())

    assert already_logged.value.error_code == "WEEKLY_SLOT_ALREADY_LOGGED"
    assert already_linked.value.error_code == "WEEKLY_MEAL_ALREADY_LINKED"


@pytest.mark.asyncio
async def test_skip_and_unskip_toggle_the_slot():
    service, uow = _service(_plan())

    skipped = await service.set_skip(_skip(True))
    unskipped = await service.set_skip(_skip(False))

    assert next(slot for slot in skipped.slots if slot.id == "slot-0-1").is_skipped
    assert not next(
        slot for slot in unskipped.slots if slot.id == "slot-0-1"
    ).is_skipped
    assert uow.meal_write_operations.reserved[0]["operation"] == (
        "weekly_meal_plan_slot_skip"
    )


@pytest.mark.asyncio
async def test_repeated_skip_is_a_no_op():
    service, uow = _service(_plan(**{"slot-0-1": {"is_skipped": True}}))

    result = await service.set_skip(_skip(True))

    assert next(slot for slot in result.slots if slot.id == "slot-0-1").is_skipped
    assert uow.weekly_meal_plans.updates == []
    assert uow.meal_write_operations.completed


@pytest.mark.asyncio
async def test_logged_slot_cannot_be_skipped_but_can_be_unskipped():
    plan = _plan(**{"slot-0-1": {"is_logged": True, "logged_meal_id": "meal-1"}})
    service, _ = _service(plan)

    with pytest.raises(ConflictException) as error:
        await service.set_skip(_skip(True))
    result = await service.set_skip(_skip(False))

    assert error.value.error_code == "WEEKLY_SLOT_ALREADY_LOGGED"
    assert next(slot for slot in result.slots if slot.id == "slot-0-1").is_logged

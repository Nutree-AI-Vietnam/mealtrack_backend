"""The background job that writes a preparing week's dishes."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from src.app.commands.meal_planner.write_weekly_plan_dishes_command import (
    WriteWeeklyPlanDishesCommand,
)
from src.app.handlers.command_handlers.meal_planner.write_weekly_plan_dishes_handler import (  # noqa: E501
    WriteWeeklyPlanDishesCommandHandler,
    _requests,
)
from src.app.services.ai_jobs.weekly_plan_executor import WeeklyPlanJobExecutor
from src.domain.exceptions.ai_job_exceptions import AiJobExecutionError
from src.domain.model.ai_jobs import AiJobClaim
from src.domain.model.ai_jobs.enums import AiJobType
from src.domain.model.weekly_meal_planner.weekly_meal_plan import WeeklyMealPlanStatus

_PAYLOAD = {
    "plan_id": "plan-1",
    "week_start_date": "2026-11-02",
    "daily_calories": 1800,
    "timezone": "Asia/Ho_Chi_Minh",
}


def _claim(payload=None):
    return AiJobClaim(
        id="job-1",
        user_id="user-1",
        job_type=AiJobType.WEEKLY_PLAN,
        language="vi",
        request_payload=payload if payload is not None else dict(_PAYLOAD),
        claim_token="token",
        attempts=1,
        max_attempts=4,
        lease_expires_at=datetime(2026, 11, 4, 12, tzinfo=UTC),
    )


class TestWhatTheJobCarries:
    @pytest.mark.asyncio
    async def test_the_payload_becomes_a_command(self):
        sent = []

        async def send(command):
            sent.append(command)
            return {"plan_id": command.plan_id, "dishes": 21}

        result = await WeeklyPlanJobExecutor(send=send).execute(_claim(), None)

        assert result["dishes"] == 21
        command = sent[0]
        assert isinstance(command, WriteWeeklyPlanDishesCommand)
        assert command.user_id == "user-1"
        assert command.week_start_date == date(2026, 11, 2)
        assert command.language == "vi"

    @pytest.mark.parametrize(
        "payload",
        [
            {**_PAYLOAD, "plan_id": ""},
            {**_PAYLOAD, "week_start_date": "not a date"},
            {**_PAYLOAD, "daily_calories": 0},
            {k: v for k, v in _PAYLOAD.items() if k != "plan_id"},
        ],
    )
    @pytest.mark.asyncio
    async def test_a_payload_that_makes_no_sense_is_refused(self, payload):
        async def send(command):  # pragma: no cover - never reached
            raise AssertionError("the command should not be sent")

        with pytest.raises(AiJobExecutionError):
            await WeeklyPlanJobExecutor(send=send).execute(_claim(payload), None)


class TestHowTheDayIsSplit:
    def test_every_slot_of_the_week_is_asked_for(self):
        requests = _requests(
            WriteWeeklyPlanDishesCommand(
                user_id="u",
                plan_id="p",
                week_start_date=date(2026, 11, 2),
                daily_calories=1800,
                timezone="Asia/Ho_Chi_Minh",
            )
        )

        assert len(requests) == 21
        assert {request.slot for request in requests} == {
            (day, slot) for day in range(7) for slot in range(3)
        }

    def test_each_slot_gets_its_locale_s_share_of_the_day(self):
        """Viet Nam eats a lighter breakfast; the shares come from the pack."""
        requests = _requests(
            WriteWeeklyPlanDishesCommand(
                user_id="u",
                plan_id="p",
                week_start_date=date(2026, 11, 2),
                daily_calories=1600,
                timezone="Asia/Ho_Chi_Minh",
                daily_protein=120.0,
            )
        )
        day_one = sorted(
            (r for r in requests if r.day_index == 0), key=lambda r: r.slot_index
        )

        assert [r.target_calories for r in day_one] == [400, 600, 600]
        assert day_one[0].protein == 30.0
        assert sum(r.target_calories for r in day_one) == 1600

    def test_a_user_we_cannot_place_gets_an_even_day(self):
        requests = _requests(
            WriteWeeklyPlanDishesCommand(
                user_id="u",
                plan_id="p",
                week_start_date=date(2026, 11, 2),
                daily_calories=2000,
                timezone="America/Los_Angeles",
            )
        )
        day_one = sorted(
            (r for r in requests if r.day_index == 0), key=lambda r: r.slot_index
        )

        assert [r.target_calories for r in day_one] == [600, 700, 700]


class _Plans:
    def __init__(self):
        self.updates = []

    async def update(self, **kwargs):
        self.updates.append(kwargs)
        return SimpleNamespace(id=kwargs.get("plan_id"))


class _Uow:
    def __init__(self, plans):
        self.weekly_meal_plans = plans
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def commit(self):
        self.committed = True


class TestWritingTheWeek:
    @pytest.mark.asyncio
    async def test_a_week_that_came_back_empty_does_not_leave_preparing(
        self, monkeypatch
    ):
        """A plan with no dishes is worse than one still being written."""
        plans = _Plans()
        handler = WriteWeeklyPlanDishesCommandHandler(
            uow_factory=lambda: _Uow(plans), generation_service=None
        )

        async def nothing(*args, **kwargs):
            from src.domain.services.weekly_meal_planner.ai_dish import WeekOfDishes

            return WeekOfDishes(dishes={}, rejected=(), unfilled=())

        monkeypatch.setattr(
            "src.app.handlers.command_handlers.meal_planner."
            "write_weekly_plan_dishes_handler.generate_week_of_dishes",
            nothing,
        )

        with pytest.raises(RuntimeError, match="WEEKLY_PLAN_NO_DISHES"):
            await handler.handle(
                WriteWeeklyPlanDishesCommand(
                    user_id="u",
                    plan_id="p",
                    week_start_date=date(2026, 11, 2),
                    daily_calories=1800,
                )
            )

        assert plans.updates == []

    @pytest.mark.asyncio
    async def test_the_dishes_are_stored_and_the_plan_becomes_a_draft(
        self, monkeypatch
    ):
        plans = _Plans()
        handler = WriteWeeklyPlanDishesCommandHandler(
            uow_factory=lambda: _Uow(plans), generation_service=None
        )

        async def one_dish(*args, **kwargs):
            from src.domain.services.weekly_meal_planner.ai_dish import (
                WeekOfDishes,
                snapshot_from_ai_recipe,
            )

            snapshot = snapshot_from_ai_recipe(
                {
                    "name": "Lemongrass tofu",
                    "ingredients": [
                        {"name": "tofu", "amount": 120, "unit": "g", "protein": 19},
                        {"name": "rice", "amount": 150, "unit": "g", "carbs": 45},
                    ],
                    "recipe_steps": [{"instruction": "Grill until cooked through."}],
                },
                dish_id="ai-1",
            )
            return WeekOfDishes(dishes={(0, 1): snapshot}, unfilled=((0, 0),))

        monkeypatch.setattr(
            "src.app.handlers.command_handlers.meal_planner."
            "write_weekly_plan_dishes_handler.generate_week_of_dishes",
            one_dish,
        )

        result = await handler.handle(
            WriteWeeklyPlanDishesCommand(
                user_id="u",
                plan_id="p",
                week_start_date=date(2026, 11, 2),
                daily_calories=1800,
            )
        )

        update = plans.updates[0]
        assert update["status"] == WeeklyMealPlanStatus.DRAFT.value
        assert list(update["saved_slots"]) == [(0, 1)]
        assert result["dishes"] == 1
        assert result["unfilled"] == [[0, 0]]

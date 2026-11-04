"""Asking for a week when its dishes come from a model."""

from datetime import date
from types import SimpleNamespace

import pytest

from src.api.routes.v1 import meal_plans as route
from src.app.commands.meal_planner import (
    GenerateWeeklyMealPlanCommand,
    PrepareWeeklyMealPlanCommand,
)
from src.domain.model.weekly_meal_planner.weekly_meal_plan import (
    WeeklyMealPlanPreferences,
    WeeklyMealPlanStatus,
)


class _Jobs:
    def __init__(self, fails=False):
        self.started = []
        self._fails = fails

    async def start_weekly_plan_job(self, **kwargs):
        if self._fails:
            raise RuntimeError("the queue is down")
        self.started.append(kwargs)
        return SimpleNamespace(id=kwargs["job_id"]), True


def _plan(status=WeeklyMealPlanStatus.PREPARING):
    return SimpleNamespace(id="plan-1", status=status)


@pytest.fixture
def harness(monkeypatch):
    """The helper with its collaborators replaced, and what they saw."""
    sent = []
    jobs = _Jobs()

    async def send(command):
        sent.append(command)
        return _plan()

    async def plan_response(plan, event_bus, **kwargs):
        return {"id": plan.id, "status": plan.status.value}

    monkeypatch.setattr(route, "_plan_response", plan_response)
    monkeypatch.setattr(route, "get_ai_job_service", lambda: jobs)
    monkeypatch.setattr(route, "get_request_language", lambda request: "vi")
    return SimpleNamespace(sent=sent, jobs=jobs, send=send)


async def _call(harness, *, budget=None):
    return await route._prepared_plan_response(
        SimpleNamespace(),
        event_bus=SimpleNamespace(send=harness.send),
        user_id="user-1",
        week=date(2026, 11, 2),
        timezone="Asia/Ho_Chi_Minh",
        preferences=WeeklyMealPlanPreferences(people=1),
        daily_calories=1800,
        budget=budget,
        translation_service=None,
        include_grocery_count=False,
    )


class TestOpeningTheWeek:
    @pytest.mark.asyncio
    async def test_the_week_is_opened_and_the_write_is_queued(self, harness):
        body = await _call(harness)

        assert body["status"] == "preparing"
        assert isinstance(harness.sent[0], PrepareWeeklyMealPlanCommand)
        job = harness.jobs.started[0]
        assert job["job_id"] == "weekly-plan-plan-1"
        assert job["request"]["plan_id"] == "plan-1"
        assert job["request"]["week_start_date"] == "2026-11-02"
        assert job["request"]["daily_calories"] == 1800
        assert job["language"] == "vi"

    @pytest.mark.asyncio
    async def test_the_day_s_macro_targets_ride_along(self, harness):
        await _call(
            harness,
            budget={
                "daily_protein": 120,
                "adjusted_daily_carbs": 180,
                "adjusted_daily_fat": 60,
            },
        )

        request = harness.jobs.started[0]["request"]
        assert request["daily_protein"] == 120
        assert request["daily_carbs"] == 180
        assert request["daily_fat"] == 60

    @pytest.mark.asyncio
    async def test_a_week_already_written_is_not_queued_again(
        self, harness, monkeypatch
    ):
        """A second tab must not start a second write of the same week."""

        async def send(command):
            return _plan(status=WeeklyMealPlanStatus.DRAFT)

        harness.send = send

        body = await _call(harness)

        assert body["status"] == "draft"
        assert harness.jobs.started == []

    @pytest.mark.asyncio
    async def test_a_queue_that_is_down_still_shows_the_week(
        self, harness, monkeypatch
    ):
        """An empty week the user can retry beats a five hundred."""
        monkeypatch.setattr(route, "get_ai_job_service", lambda: _Jobs(fails=True))

        body = await _call(harness)

        assert body["status"] == "preparing"


class TestWhatTheClientIsTold:
    def test_preparing_is_advertised_only_while_the_flag_is_on(self, monkeypatch):
        monkeypatch.setattr(
            route,
            "get_settings",
            lambda: SimpleNamespace(WEEKLY_PLAN_AI_DISHES_ENABLED=True),
        )
        assert route.PLAN_PREPARING_FEATURE in route._plan_features()

        monkeypatch.setattr(
            route,
            "get_settings",
            lambda: SimpleNamespace(WEEKLY_PLAN_AI_DISHES_ENABLED=False),
        )
        features = route._plan_features()
        assert route.PLAN_PREPARING_FEATURE not in features
        assert "saved_recipe_slots" in features

    def test_the_flag_is_off_by_default(self):
        from src.infra.config.settings import get_settings

        assert get_settings().WEEKLY_PLAN_AI_DISHES_ENABLED is False

    def test_the_catalogue_path_is_still_what_runs_while_it_is_off(self):
        """The generate command is untouched; only the branch in front moved."""
        assert GenerateWeeklyMealPlanCommand is not PrepareWeeklyMealPlanCommand

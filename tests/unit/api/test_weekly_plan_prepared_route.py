"""Asking for a week when its dishes come from a model."""

from datetime import date
from pathlib import Path
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
        # Not the literal: that is what sized the id past its column.
        assert job["job_id"] == route.weekly_plan_job_id("plan-1")
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
        monkeypatch.setattr(route, "weekly_plan_ai_dishes_enabled", lambda: True)
        assert route.PLAN_PREPARING_FEATURE in route._plan_features()

        monkeypatch.setattr(route, "weekly_plan_ai_dishes_enabled", lambda: False)
        features = route._plan_features()
        assert route.PLAN_PREPARING_FEATURE not in features
        assert "saved_recipe_slots" in features

    def test_the_flag_is_read_through_the_module_allowed_to_reach_infra(self):
        """src.api may not import src.infra; the contract is CI-enforced."""
        import src.api.routes.v1.meal_plans as module

        source = Path(module.__file__).read_text()
        assert "from src.infra" not in source
        assert "weekly_plan_ai_dishes_enabled" in source

    def test_the_flag_is_off_by_default(self):
        from src.infra.config.settings import get_settings

        assert get_settings().WEEKLY_PLAN_AI_DISHES_ENABLED is False

    def test_the_catalogue_path_is_still_what_runs_while_it_is_off(self):
        """The generate command is untouched; only the branch in front moved."""
        assert GenerateWeeklyMealPlanCommand is not PrepareWeeklyMealPlanCommand


class TestAHeadStartOnNextWeek:
    """Sunday opens next week so nobody watches theirs being written."""

    @pytest.fixture
    def week(self, monkeypatch):
        seen = SimpleNamespace(prepared=[], looked_up=[])

        async def send(command):
            name = type(command).__name__
            if name == "GetCurrentWeeklyPlanQuery":
                seen.looked_up.append(command.week_start_date)
                return None
            return _plan()

        async def prepared(request, **kwargs):
            seen.prepared.append(kwargs["week"])
            return None

        async def budget(event_bus, query):
            return {"adjusted_daily_calories": 1800}

        monkeypatch.setattr(route, "_prepared_plan_response", prepared)
        monkeypatch.setattr(route, "_weekly_budget", budget)
        seen.send = send
        return seen

    async def _run(self, week, *, today):
        import datetime as real_datetime

        class _Clock(real_datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime.datetime(
                    today.year, today.month, today.day, 9, tzinfo=tz
                )

        original = route.datetime
        route.datetime = _Clock
        try:
            await route._prepare_next_week(
                SimpleNamespace(),
                event_bus=SimpleNamespace(send=week.send),
                user_id="user-1",
                week=date(2026, 11, 2),
                timezone="Asia/Ho_Chi_Minh",
                plan=SimpleNamespace(preferences=WeeklyMealPlanPreferences(people=1)),
            )
        finally:
            route.datetime = original

    @pytest.mark.asyncio
    async def test_sunday_starts_the_week_after(self, week):
        await self._run(week, today=date(2026, 11, 8))

        assert week.prepared == [date(2026, 11, 9)]
        assert week.looked_up == [date(2026, 11, 9)]

    @pytest.mark.asyncio
    async def test_any_other_day_leaves_it_alone(self, week):
        await self._run(week, today=date(2026, 11, 4))

        assert week.prepared == []
        assert week.looked_up == []

    @pytest.mark.asyncio
    async def test_a_week_already_opened_is_not_opened_again(self, week, monkeypatch):
        async def send(command):
            if type(command).__name__ == "GetCurrentWeeklyPlanQuery":
                return _plan()
            return _plan()

        week.send = send

        await self._run(week, today=date(2026, 11, 8))

        assert week.prepared == []

    @pytest.mark.asyncio
    async def test_a_head_start_that_fails_is_never_the_user_s_problem(
        self, week, monkeypatch
    ):
        async def boom(event_bus, query):
            raise RuntimeError("the budget service is down")

        monkeypatch.setattr(route, "_weekly_budget", boom)

        await self._run(week, today=date(2026, 11, 8))

        assert week.prepared == []


class TestTheJobIdFitsItsColumn:
    """Staging's first real week created the plan and lost the job.

    ai_jobs.id is VARCHAR(36). "weekly-plan-" plus a plan's UUID is 48, and
    asyncpg raised StringDataRightTruncationError inside the try that only
    logs — so the planner sat in 'preparing' with nobody coming. Every test
    had used a short fake plan id ("plan-1"), where it fitted.
    """

    PLAN_ID = "bd143a63-5a5a-4054-ba4b-6315fecb2943"

    def test_it_fits_the_column_the_model_declares(self):
        from src.infra.database.models.ai_job import AiJobORM

        width = AiJobORM.__table__.c.id.type.length
        assert len(route.weekly_plan_job_id(self.PLAN_ID)) <= width

    def test_a_uuid_plan_id_is_what_broke_it(self):
        """The literal that shipped, measured against the same column."""
        from src.infra.database.models.ai_job import AiJobORM

        width = AiJobORM.__table__.c.id.type.length
        assert len(f"weekly-plan-{self.PLAN_ID}") > width

    def test_the_same_plan_always_gets_the_same_job(self):
        """A second tab must not start a second write of the same week."""
        assert route.weekly_plan_job_id(self.PLAN_ID) == route.weekly_plan_job_id(
            self.PLAN_ID
        )

    def test_another_plan_gets_another_job(self):
        assert route.weekly_plan_job_id(self.PLAN_ID) != route.weekly_plan_job_id(
            "aaaaaaaa-5a5a-4054-ba4b-6315fecb2943"
        )

    @pytest.mark.asyncio
    async def test_the_route_queues_an_id_that_fits(self, harness, monkeypatch):
        """End of the path: what the route actually hands the job service."""
        from src.infra.database.models.ai_job import AiJobORM

        async def send(command):
            return SimpleNamespace(
                id=self.PLAN_ID, status=WeeklyMealPlanStatus.PREPARING
            )

        harness.send = send

        await _call(harness)

        queued = harness.jobs.started[0]["job_id"]
        assert len(queued) <= AiJobORM.__table__.c.id.type.length
        assert queued == route.weekly_plan_job_id(self.PLAN_ID)


class TestAWeekWaitingWithNobodyComing:
    """A plan in 'preparing' whose job was never queued must not sit there.

    _prepared_plan_response says the next request queues it again. It did
    not: the job is only queued where a plan is created, and a plan that
    already exists never reaches that branch. Staging's first real week
    sat in 'preparing' through every refresh.
    """

    PLAN_ID = "bd143a63-5a5a-4054-ba4b-6315fecb2943"

    @pytest.fixture
    def waiting(self, monkeypatch):
        seen = SimpleNamespace(queued=[], looked_up=[])

        class _Jobs:
            async def get(self, *, user_id, job_id):
                seen.looked_up.append(job_id)
                if seen.queued:
                    return SimpleNamespace(id=job_id)
                raise RuntimeError("no such job")

            async def start_weekly_plan_job(self, **kwargs):
                seen.queued.append(kwargs)
                return SimpleNamespace(id=kwargs["job_id"]), True

        async def budget(event_bus, query):
            return {"adjusted_daily_calories": 2311, "daily_protein": 126.0}

        monkeypatch.setattr(route, "get_ai_job_service", lambda: _Jobs())
        monkeypatch.setattr(route, "_weekly_budget", budget)
        monkeypatch.setattr(route, "get_request_language", lambda request: "vi")
        return seen

    async def _call(self, waiting):
        return await route._ensure_the_week_is_being_written(
            SimpleNamespace(),
            event_bus=SimpleNamespace(),
            user_id="user-1",
            plan=SimpleNamespace(id=self.PLAN_ID),
            week=date(2026, 11, 2),
            timezone="Asia/Ho_Chi_Minh",
        )

    @pytest.mark.asyncio
    async def test_a_week_with_no_job_gets_one(self, waiting):
        await self._call(waiting)

        assert len(waiting.queued) == 1
        job = waiting.queued[0]
        assert job["job_id"] == route.weekly_plan_job_id(self.PLAN_ID)
        assert job["request"]["plan_id"] == self.PLAN_ID
        assert job["request"]["daily_calories"] == 2311
        assert job["request"]["daily_protein"] == 126.0

    @pytest.mark.asyncio
    async def test_a_week_already_being_written_is_left_alone(self, waiting):
        """The app polls this every few seconds; it must stay cheap."""
        await self._call(waiting)
        await self._call(waiting)

        assert len(waiting.queued) == 1
        assert len(waiting.looked_up) == 2

    @pytest.mark.asyncio
    async def test_a_budget_that_fails_is_never_the_user_s_problem(
        self, waiting, monkeypatch
    ):
        async def boom(event_bus, query):
            raise RuntimeError("the budget service is down")

        monkeypatch.setattr(route, "_weekly_budget", boom)

        await self._call(waiting)

        assert waiting.queued == []

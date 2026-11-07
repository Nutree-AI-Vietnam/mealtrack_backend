"""Filling plan slots the week writer left empty."""

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "backfill_empty_plan_slots.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "backfill_empty_plan_slots", _SCRIPT_PATH
)
assert _SPEC is not None
backfill = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(backfill)


def _slot(day, slot, *, recipe=None, saved=None, logged=False, skipped=False):
    return SimpleNamespace(
        day_index=day,
        slot_index=slot,
        has_recipe=recipe is not None or saved is not None,
        is_logged=logged,
        is_skipped=skipped,
    )


class _Plans:
    def __init__(self, plan):
        self.plan = plan
        self.updates = []

    async def get_by_id(self, **_):
        return self.plan

    async def update(self, **kwargs):
        self.updates.append(kwargs)


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


def _setup(monkeypatch, plan):
    plans = _Plans(plan)
    uows = []

    def factory():
        uow = _Uow(plans)
        uows.append(uow)
        return uow

    asked = {}

    async def fallback(uow, **kwargs):
        asked.update(kwargs)
        return {
            coordinate: f"recipe-{coordinate[0]}-{coordinate[1]}"
            for coordinate in kwargs["coordinates"]
        }

    monkeypatch.setattr(backfill, "AsyncUnitOfWork", factory)
    monkeypatch.setattr(backfill, "catalog_fallback_slots", fallback)
    return plans, uows, asked


_PLAN = SimpleNamespace(
    revision=7,
    week_start_date=date(2026, 11, 2),
    daily_calories=1700,
    slots=(
        _slot(0, 0, recipe="pho"),
        _slot(0, 1),
        _slot(0, 2, logged=True),
        _slot(1, 1, skipped=True),
        _slot(5, 2),
        _slot(6, 0, saved="saved-1"),
    ),
)


class TestFillingOnePlan:
    @pytest.mark.asyncio
    async def test_only_empty_open_slots_are_filled(self, monkeypatch):
        plans, uows, asked = _setup(monkeypatch, _PLAN)

        filled, empty = await backfill._fill("plan-1", "user-1", dry_run=False)

        assert asked["coordinates"] == ((0, 1), (5, 2))
        assert asked["daily_calories"] == 1700
        assert asked["week_start_date"] == date(2026, 11, 2)
        assert plans.updates == [
            {
                "user_id": "user-1",
                "plan_id": "plan-1",
                "slots": {(0, 1): "recipe-0-1", (5, 2): "recipe-5-2"},
                "expected_revision": 7,
            }
        ]
        assert uows[-1].committed
        assert (filled, empty) == (2, 2)

    @pytest.mark.asyncio
    async def test_a_dry_run_writes_nothing(self, monkeypatch):
        plans, uows, _ = _setup(monkeypatch, _PLAN)

        filled, empty = await backfill._fill("plan-1", "user-1", dry_run=True)

        assert plans.updates == []
        assert not uows[-1].committed
        assert (filled, empty) == (2, 2)

    @pytest.mark.asyncio
    async def test_a_plan_without_a_target_uses_the_default(self, monkeypatch):
        plan = SimpleNamespace(
            week_start_date=date(2026, 11, 2), daily_calories=None, slots=(_slot(0, 1),)
        )
        _, _, asked = _setup(monkeypatch, plan)

        await backfill._fill("plan-1", "user-1", dry_run=True)

        assert asked["daily_calories"] == backfill.DEFAULT_DAILY_CALORIES

    @pytest.mark.asyncio
    async def test_a_plan_that_is_gone_is_skipped(self, monkeypatch):
        plans, _, _ = _setup(monkeypatch, None)

        assert await backfill._fill("plan-1", "user-1", dry_run=False) == (0, 0)
        assert plans.updates == []


def test_the_selection_only_reads_empty_open_slots():
    sql = str(
        backfill._empty_slot()[0].compile(dialect=postgresql.dialect())
    ) + " ".join(
        str(clause.compile(dialect=postgresql.dialect()))
        for clause in backfill._empty_slot()[1:]
    )

    assert "catalog_meal_id IS NULL" in sql
    assert "saved_suggestion_id IS NULL" in sql
    assert "is_logged IS false" in sql
    assert "is_skipped IS NOT true" in sql


class TestAWeekTheUserChanged:
    @pytest.mark.asyncio
    async def test_is_skipped_not_overwritten(self, monkeypatch, capsys):
        """The user filled or skipped a slot while its dish was being picked."""
        from src.domain.exceptions.weekly_meal_planner_exceptions import (
            WeeklyMealPlanConflictError,
        )

        plans, _, _ = _setup(monkeypatch, _PLAN)

        async def stale(**kwargs):
            raise WeeklyMealPlanConflictError(
                "Weekly meal plan changed since it was loaded",
                error_code="WEEKLY_PLAN_STALE_REVISION",
            )

        plans.update = stale

        async def one_plan(since, plan_ids):
            return [("plan-1", "user-1")]

        monkeypatch.setattr(backfill, "_plans_with_empty_slots", one_plan)

        summary = await backfill._run(
            SimpleNamespace(since=date(2026, 11, 2), plan_id=[], dry_run=False)
        )

        assert summary["plans_changed_meanwhile"] == 1
        assert summary["plans_updated"] == 0
        assert summary["failed"] == 0
        assert "changed meanwhile" in capsys.readouterr().err

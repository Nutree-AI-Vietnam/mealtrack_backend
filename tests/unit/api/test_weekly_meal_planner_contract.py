from datetime import date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.meal_plans import router
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)


def _plan():
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=2,
        preferences=WeeklyMealPlanPreferences(people=2),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"slot-{day}-{slot}",
                day_index=day,
                slot_index=slot,
                recipe_id=None,
            )
            for day in range(7)
            for slot in range(2)
        ),
        created_at=datetime(2026, 9, 20),
        updated_at=datetime(2026, 9, 20),
    )


class _Bus:
    async def send(self, query):
        if query.__class__.__name__ == "GetUserTimezoneQuery":
            return "UTC"
        if query.__class__.__name__ == "GetCurrentWeeklyPlanQuery":
            return _plan()
        if query.__class__.__name__ == "GetRecipeDetailQuery":
            return None
        if query.__class__.__name__ == "GetWeeklyGroceriesQuery":
            return (_plan(), ())
        raise AssertionError(f"unexpected query: {query!r}")


def _app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_configured_event_bus] = lambda: _Bus()
    return app


def test_current_plan_returns_fourteen_slots():
    response = TestClient(_app()).get(
        "/v1/meal-plans/current?week_start_date=2026-09-21"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 1
    assert len(body["plan"]) == 7
    assert sum(len(day) for day in body["plan"]) == 14
    assert body["plan"][0][0]["slot_name"] == "lunch"
    assert body["plan"][0][1]["slot_name"] == "dinner"


def test_current_plan_without_week_start_resolves_to_current_monday():
    response = TestClient(_app()).get("/v1/meal-plans/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "plan-1"
    assert "to_buy_count" in body


def test_current_plan_rejects_non_monday_week():
    response = TestClient(_app()).get(
        "/v1/meal-plans/current?week_start_date=2026-09-22"
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "WEEK_START_NOT_MONDAY"


def test_patch_rejects_duplicate_slot_coordinates_before_dispatch():
    response = TestClient(_app()).patch(
        "/v1/meal-plans/plan-1",
        headers={"Idempotency-Key": "patch-1"},
        json={
            "slots": [
                {"day_index": 0, "slot_index": 0, "recipe_id": None},
                {"day_index": 0, "slot_index": 0, "recipe_id": None},
            ]
        },
    )

    assert response.status_code == 422

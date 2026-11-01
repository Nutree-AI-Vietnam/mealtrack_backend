"""Route-level 422 for inverted / malformed progress summary dates."""

from datetime import date
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1 import progress
from src.api.routes.v1.progress import router


def _client(send=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    bus = AsyncMock()
    bus.send = send or AsyncMock(
        return_value={
            "effective_start": "2026-08-26",
            "effective_end": "2026-09-01",
            "cap_days": 400,
            "days": [],
        }
    )
    app.dependency_overrides[get_configured_event_bus] = lambda: bus
    return TestClient(app), bus


def test_inverted_dates_return_422_without_dispatch():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"start_date": "2026-09-02", "end_date": "2026-09-01"},
    )
    assert response.status_code == 422
    bus.send.assert_not_awaited()


def test_malformed_date_returns_422():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"start_date": "not-a-date", "end_date": "2026-09-01"},
    )
    assert response.status_code == 422
    bus.send.assert_not_called()


def test_valid_range_dispatches_query():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"start_date": "2026-08-26", "end_date": "2026-09-01"},
    )
    assert response.status_code == 200
    query = bus.send.await_args.args[0]
    assert query.start_date == date(2026, 8, 26)
    assert query.end_date == date(2026, 9, 1)


_DAY = {
    "date": "2026-09-01",
    "protein_g": 10.0,
    "carbs_g": 20.0,
    "fat_g": 5.0,
    "calories": 165.0,
    "target_calories": 1850.0,
    "target_source": "base",
    "burned_calories": 0.0,
    "hydration_ml": 0,
    "hydration_goal_ml": 2000,
    "protein_target_g": 140.0,
    "meal_count": 1,
    "logged_status": "partial",
    "is_cheat_day": False,
}


def _summary(**extra):
    return {
        "effective_start": "2026-09-01",
        "effective_end": "2026-09-01",
        "cap_days": 400,
        **extra,
    }


def test_include_trends_is_passed_to_the_query():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={
            "start_date": "2026-08-26",
            "end_date": "2026-09-01",
            "include": "trends",
        },
    )
    assert response.status_code == 200
    assert bus.send.await_args.args[0].include_trends is True


def test_without_include_the_query_stays_default():
    client, bus = _client()
    client.get(
        "/v1/progress/summary",
        params={"start_date": "2026-08-26", "end_date": "2026-09-01"},
    )
    assert bus.send.await_args.args[0].include_trends is False


def test_trends_window_up_to_42_days_is_accepted():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={
            "start_date": "2026-07-22",
            "end_date": "2026-09-01",
            "include": "trends",
        },
    )
    assert response.status_code == 200


def test_trends_window_over_42_days_returns_422_without_dispatch():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={
            "start_date": "2026-07-21",
            "end_date": "2026-09-01",
            "include": "trends",
        },
    )
    assert response.status_code == 422
    bus.send.assert_not_awaited()


def test_trends_without_both_dates_returns_422():
    client, bus = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"end_date": "2026-09-01", "include": "trends"},
    )
    assert response.status_code == 422
    bus.send.assert_not_awaited()


def test_long_default_window_is_still_allowed():
    client, _ = _client()
    response = client.get(
        "/v1/progress/summary",
        params={"start_date": "2026-01-01", "end_date": "2026-09-01"},
    )
    assert response.status_code == 200


def test_summary_route_is_rate_limited():
    assert progress.PROGRESS_SUMMARY_LIMIT == "60/minute"
    assert getattr(progress.get_progress_summary, "__wrapped__", None) is not None


def test_default_payload_has_no_trends_keys():
    client, _ = _client(send=AsyncMock(return_value=_summary(days=[_DAY])))
    body = client.get("/v1/progress/summary").json()
    assert set(body) == {"effective_start", "effective_end", "cap_days", "days"}
    assert "micros" not in body["days"][0]
    assert "movement_load" not in body["days"][0]
    # Fields that existed before keep sending null.
    assert body["days"][0]["iron_mg"] is None


def test_trends_payload_carries_the_new_fields():
    day = {
        **_DAY,
        "micros": {"iron": 3.5},
        "micro_meal_counts": {"iron": 2},
        "meal_quality_counts": {"good": 1, "fair": 0, "low": 1},
        "quality_by_meal_type": {"lunch": 62.0},
        "movement_minutes": 30,
        "movement_sessions": 1,
        "movement_load": 60.0,
        "movement_kinds": ["run"],
    }
    payload = _summary(
        days=[day],
        goals_revision=3,
        micro_goals={"iron": 17.4},
        micro_upper_limits={"iron": {"value": 45.0, "scope": "all_sources"}},
    )
    client, _ = _client(send=AsyncMock(return_value=payload))
    body = client.get(
        "/v1/progress/summary",
        params={
            "start_date": "2026-09-01",
            "end_date": "2026-09-01",
            "include": "trends",
        },
    ).json()
    assert body["goals_revision"] == 3
    assert body["micro_goals"] == {"iron": 17.4}
    assert body["micro_upper_limits"]["iron"]["scope"] == "all_sources"
    assert body["days"][0]["micro_meal_counts"] == {"iron": 2}
    assert body["days"][0]["meal_quality_counts"]["low"] == 1

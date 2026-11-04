"""Targets, pace ranges and My Plan metrics follow a saved gain pace."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.handlers.query_handlers.get_user_metrics_query_handler import (
    GetUserMetricsQueryHandler,
)
from src.app.handlers.query_handlers.get_user_profile_query_handler import (
    GetUserProfileQueryHandler,
)
from src.app.handlers.query_handlers.get_user_tdee_query_handler import (
    GetUserTdeeQueryHandler,
)
from src.app.handlers.query_handlers.preview_tdee_query_handler import (
    PreviewTdeeQueryHandler,
)
from src.app.queries.tdee import GetUserTdeeQuery, PreviewTdeeQuery
from src.app.queries.user import GetUserMetricsQuery, GetUserProfileQuery

_QUERIES = "src.app.handlers.query_handlers"


def _bulk_profile(gain: float | None) -> MagicMock:
    return MagicMock(
        age=30,
        gender="male",
        height_cm=175.0,
        weight_kg=72.0,
        body_fat_percentage=None,
        job_type="on_feet",
        training_days_per_week=0,
        training_minutes_per_session=0,
        training_level="beginner",
        fitness_goal="bulk",
        dietary_preferences=[],
        has_custom_macros=False,
        weight_loss_kg_per_week=None,
        weight_gain_kg_per_week=gain,
        health_conditions=[],
        profile_target_revision=1,
        created_at=None,
        updated_at=None,
    )


def _uow_returning(profile) -> MagicMock:
    rows = MagicMock()
    rows.scalars.return_value.first.return_value = profile
    uow = MagicMock()
    uow.session.execute = AsyncMock(return_value=rows)
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    return uow


async def _user_tdee(gain: float | None) -> dict:
    """The target the weekly budget and day totals are built from."""
    with patch(
        f"{_QUERIES}.get_user_tdee_query_handler.AsyncUnitOfWork",
        return_value=_uow_returning(_bulk_profile(gain)),
    ):
        return await GetUserTdeeQueryHandler()._compute_tdee(
            GetUserTdeeQuery(user_id="user-1")
        )


@pytest.mark.asyncio
async def test_user_target_adds_the_gain_pace_surplus():
    flat = await _user_tdee(None)
    half_kg = await _user_tdee(0.5)

    # 0.5 kg a week adds about 550 kcal a day, against the flat bulk's 300.
    assert half_kg["target_calories"] - flat["target_calories"] == pytest.approx(
        250, abs=5
    )
    assert half_kg["pace_range"]["max_kg_per_week"] == 1.0
    assert half_kg["pace_range"]["recommended_high_kg"] == 0.36


@pytest.mark.asyncio
async def test_user_target_explains_a_blocked_gain_pace():
    profile = _bulk_profile(0.5)
    profile.health_conditions = ["breastfeeding"]
    with patch(
        f"{_QUERIES}.get_user_tdee_query_handler.AsyncUnitOfWork",
        return_value=_uow_returning(profile),
    ):
        result = await GetUserTdeeQueryHandler()._compute_tdee(
            GetUserTdeeQuery(user_id="user-1")
        )
    flat = await _user_tdee(None)

    assert result["pace_range"] is None
    assert result["gain_pace_blocked_reason"] == "health_condition"
    assert result["target_calories"] == pytest.approx(flat["target_calories"], abs=1)


@pytest.mark.asyncio
async def test_preview_blocks_the_gain_pace_for_an_under_18():
    result = await PreviewTdeeQueryHandler().handle(
        PreviewTdeeQuery(
            age=16,
            sex="male",
            height=170,
            weight=60,
            job_type="on_feet",
            training_days_per_week=0,
            training_minutes_per_session=0,
            goal="bulk",
            weight_gain_kg_per_week=1.0,
        )
    )

    assert result["pace_range"] is None
    assert result["gain_pace_blocked_reason"] == "minor"


@pytest.mark.asyncio
async def test_profile_tdee_uses_the_saved_gain_pace():
    async def calories(gain):
        with patch(
            f"{_QUERIES}.get_user_profile_query_handler.AsyncUnitOfWork",
            return_value=_uow_returning(_bulk_profile(gain)),
        ):
            result = await GetUserProfileQueryHandler()._fetch_from_db(
                GetUserProfileQuery(user_id="u1")
            )
        return result["tdee"]["macros"]["calories"]

    assert await calories(0.5) - await calories(None) == pytest.approx(250, abs=5)


@pytest.mark.asyncio
async def test_preview_returns_the_gain_range_and_the_paced_surplus():
    def query(**overrides) -> PreviewTdeeQuery:
        values = {
            "age": 30,
            "sex": "male",
            "height": 175,
            "weight": 72,
            "job_type": "on_feet",
            "training_days_per_week": 0,
            "training_minutes_per_session": 0,
            "goal": "bulk",
        }
        values.update(overrides)
        return PreviewTdeeQuery(**values)

    handler = PreviewTdeeQueryHandler()
    flat = await handler.handle(query())
    paced = await handler.handle(query(weight_gain_kg_per_week=1.0))
    recomp = await handler.handle(query(goal="recomp"))

    assert paced["pace_range"] == {
        "min_kg_per_week": 0.25,
        "max_kg_per_week": 1.0,
        "recommended_low_kg": 0.25,
        "recommended_high_kg": 0.36,
    }
    assert paced["macros"]["calories"] - flat["macros"]["calories"] == pytest.approx(
        800, abs=5
    )
    assert recomp["pace_range"] is None


def _metrics_profile(gain: float | None) -> SimpleNamespace:
    return SimpleNamespace(
        age=30,
        date_of_birth=None,
        gender="male",
        height_cm=175.0,
        weight_kg=72.0,
        body_fat_percentage=None,
        job_type="desk",
        training_days_per_week=0,
        training_minutes_per_session=0,
        training_level=None,
        fitness_goal="bulk",
        target_weight_kg=None,
        goal_start_weight_kg=None,
        goal_started_at=None,
        journey_progress_seed_percent=0.0,
        updated_at=datetime(2026, 11, 1, tzinfo=UTC),
        weight_loss_kg_per_week=None,
        weight_gain_kg_per_week=gain,
        dietary_preferences=[],
        allergies=[],
        health_conditions=[],
        pace_clinician_disclaimer=False,
        profile_target_revision=1,
    )


async def _metrics(gain: float | None) -> dict:
    with patch(
        f"{_QUERIES}.get_user_metrics_query_handler.AsyncUnitOfWork",
        return_value=_uow_returning(_metrics_profile(gain)),
    ):
        return await GetUserMetricsQueryHandler()._compute(
            GetUserMetricsQuery(user_id="u1")
        )


@pytest.mark.asyncio
async def test_metrics_return_the_saved_gain_pace_in_kg_and_lb():
    metrics = await _metrics(0.5)

    assert metrics["weight_gain_kg_per_week"] == 0.5
    assert metrics["weight_gain_lb_per_week"] == 1.1


@pytest.mark.asyncio
async def test_metrics_keep_the_gain_keys_when_no_pace_is_saved():
    metrics = await _metrics(None)

    assert "weight_gain_kg_per_week" in metrics
    assert metrics["weight_gain_kg_per_week"] is None
    assert metrics["weight_gain_lb_per_week"] is None

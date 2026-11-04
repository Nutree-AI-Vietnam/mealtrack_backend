"""GET metrics carries date_of_birth, and caches it as a plain string."""

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.handlers.query_handlers.get_user_metrics_query_handler import (
    GetUserMetricsQueryHandler,
)
from src.app.queries.user import GetUserMetricsQuery
from src.infra.cache.cache_service import _json_serializer

_HANDLER = "src.app.handlers.query_handlers.get_user_metrics_query_handler"


def _profile(date_of_birth: date | None) -> SimpleNamespace:
    return SimpleNamespace(
        age=30,
        date_of_birth=date_of_birth,
        gender="female",
        height_cm=160.0,
        weight_kg=55.0,
        body_fat_percentage=None,
        job_type="desk",
        training_days_per_week=3,
        training_minutes_per_session=45,
        training_level=None,
        fitness_goal="recomp",
        target_weight_kg=None,
        goal_start_weight_kg=None,
        goal_started_at=None,
        journey_progress_seed_percent=0.0,
        updated_at=datetime(2026, 11, 1, tzinfo=UTC),
        weight_loss_kg_per_week=None,
        weight_gain_kg_per_week=None,
        dietary_preferences=[],
        allergies=[],
        health_conditions=[],
        pace_clinician_disclaimer=False,
        profile_target_revision=4,
    )


async def _compute(profile: SimpleNamespace) -> dict:
    result = MagicMock()
    result.scalars.return_value.first.return_value = profile
    uow = MagicMock()
    uow.session.execute = AsyncMock(return_value=result)
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    with patch(f"{_HANDLER}.AsyncUnitOfWork", return_value=uow):
        return await GetUserMetricsQueryHandler()._compute(
            GetUserMetricsQuery(user_id="u1")
        )


@pytest.mark.asyncio
async def test_stored_date_of_birth_is_returned_as_an_iso_date():
    metrics = await _compute(_profile(date(1996, 3, 1)))

    assert metrics["date_of_birth"] == "1996-03-01"
    # The cache's JSON encoder has no date support; the payload must still fit.
    assert json.loads(json.dumps(metrics, default=_json_serializer))[
        "date_of_birth"
    ] == ("1996-03-01")


@pytest.mark.asyncio
async def test_no_stored_date_is_null_not_missing():
    metrics = await _compute(_profile(None))

    assert "date_of_birth" in metrics
    assert metrics["date_of_birth"] is None


@pytest.mark.asyncio
async def test_payload_cached_before_date_of_birth_existed_is_rebuilt():
    """Otherwise a user with a stored date would read null until the TTL."""
    cache = MagicMock()
    cache.get_json = AsyncMock(
        return_value={"user_id": "u1", "age": 30, "profile_target_revision": 4}
    )
    cache.set_json = AsyncMock()
    handler = GetUserMetricsQueryHandler(cache_service=cache)
    fresh = {
        "user_id": "u1",
        "date_of_birth": "1996-03-01",
        "profile_target_revision": 4,
    }

    with (
        patch.object(handler, "_current_profile_revision", AsyncMock(return_value=4)),
        patch.object(handler, "_compute", AsyncMock(return_value=fresh)),
    ):
        result = await handler.handle(GetUserMetricsQuery(user_id="u1"))

    assert result == fresh
    cache.set_json.assert_awaited_once()

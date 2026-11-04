"""GetUserProfileQueryHandler builds the profile's TDEE with the saved pace."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.handlers.query_handlers.get_user_profile_query_handler import (
    GetUserProfileQueryHandler,
)
from src.app.queries.user import GetUserProfileQuery


def _profile(pace: float | None) -> MagicMock:
    return MagicMock(
        age=30,
        gender="male",
        height_cm=175,
        weight_kg=72,
        body_fat_percentage=None,
        job_type="on_feet",
        training_days_per_week=0,
        training_minutes_per_session=0,
        training_level="beginner",
        fitness_goal="cut",
        weight_loss_kg_per_week=pace,
        created_at=None,
        updated_at=None,
    )


async def _target_calories(pace: float | None) -> float:
    rows = MagicMock()
    rows.scalars.return_value.first.return_value = _profile(pace)
    with patch(
        "src.app.handlers.query_handlers.get_user_profile_query_handler.AsyncUnitOfWork"
    ) as uow_class:
        uow = uow_class.return_value.__aenter__.return_value
        uow.session.execute = AsyncMock(return_value=rows)
        result = await GetUserProfileQueryHandler()._fetch_from_db(
            GetUserProfileQuery(user_id="u1")
        )
    return result["tdee"]["macros"]["calories"]


@pytest.mark.asyncio
async def test_profile_tdee_uses_the_saved_pace():
    flat = await _target_calories(None)
    half_kg = await _target_calories(0.5)
    # 0.5 kg a week costs about 550 kcal a day, against the flat cut's 300.
    assert flat - half_kg == pytest.approx(250, abs=5)

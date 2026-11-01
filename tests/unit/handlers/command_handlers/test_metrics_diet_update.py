"""My Plan can change the diet, which feeds the personal nutrient goals."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.request.user_profile_update_requests import UpdateMetricsRequest
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.handlers.command_handlers.update_user_metrics_command_handler import (
    UpdateUserMetricsCommandHandler,
)
from src.domain.model.user.core_user import UserProfileDomainModel


def _profile(**overrides) -> UserProfileDomainModel:
    values = {
        "id": str(uuid4()),
        "user_id": "test_user",
        "age": 30,
        "gender": "female",
        "height_cm": 160.0,
        "weight_kg": 55.0,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 45,
        "fitness_goal": "recomp",
        "meals_per_day": 3,
        "dietary_preferences": ["classic"],
        "profile_target_revision": 4,
    }
    values.update(overrides)
    return UserProfileDomainModel(**values)


def _uow(profile: UserProfileDomainModel) -> MagicMock:
    uow = MagicMock()
    uow.users.get_profile = AsyncMock(return_value=profile)
    uow.users.update_profile = AsyncMock(side_effect=lambda p: p)
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    return uow


async def _update(profile: UserProfileDomainModel, diets: list[str]) -> None:
    handler = UpdateUserMetricsCommandHandler(
        _uow(profile), event_publisher=MagicMock(publish=AsyncMock())
    )
    await handler.handle(
        UpdateUserMetricsCommand(user_id="test_user", dietary_preferences=diets)
    )


@pytest.mark.asyncio
async def test_diet_change_is_saved_and_bumps_the_target_revision():
    profile = _profile()

    await _update(profile, ["vegetarian"])

    assert profile.dietary_preferences == ["vegetarian"]
    assert profile.profile_target_revision == 5


@pytest.mark.asyncio
async def test_same_diet_keeps_the_revision():
    profile = _profile()

    await _update(profile, [" classic ", "classic"])

    assert profile.dietary_preferences == ["classic"]
    assert profile.profile_target_revision == 4


@pytest.mark.asyncio
async def test_diet_alone_counts_as_a_metric_update():
    profile = _profile(dietary_preferences=[])

    await _update(profile, ["vegan", ""])

    assert profile.dietary_preferences == ["vegan"]
    assert profile.profile_target_revision == 5


def test_request_accepts_a_diet_list_and_omits_it_by_default():
    assert UpdateMetricsRequest().dietary_preferences is None
    request = UpdateMetricsRequest(dietary_preferences=["vegan"])
    assert request.dietary_preferences == ["vegan"]


@pytest.mark.parametrize("diets", [["x" * 41], [f"diet{i}" for i in range(11)]])
def test_request_rejects_long_diet_names_and_long_lists(diets):
    with pytest.raises(ValidationError):
        UpdateMetricsRequest(dietary_preferences=diets)

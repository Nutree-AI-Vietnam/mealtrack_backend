from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.app.commands.user.save_user_onboarding_command import SaveUserOnboardingCommand
from src.app.handlers.command_handlers.save_user_onboarding_command_handler import (
    SaveUserOnboardingCommandHandler,
)


def _profile(revision=1):
    return SimpleNamespace(
        age=30,
        gender="male",
        height_cm=175.0,
        weight_kg=70.0,
        body_fat_percentage=None,
        date_of_birth=None,
        target_weight_kg=None,
        goal_start_weight_kg=None,
        goal_started_at=None,
        job_type="desk",
        training_days_per_week=3,
        training_minutes_per_session=45,
        fitness_goal="recomp",
        meals_per_day=3,
        pain_points=[],
        dietary_preferences=[],
        training_level="beginner",
        referral_sources=[],
        challenge_duration=None,
        training_types=None,
        custom_protein_g=None,
        custom_carbs_g=None,
        custom_fat_g=None,
        profile_target_revision=revision,
    )


def _uow(profile):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.find_by_id = AsyncMock(return_value=object())
    uow.users.get_profile = AsyncMock(return_value=profile)
    uow.users.update_profile = AsyncMock()
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    return uow


def _command(user_id, **overrides):
    values = {
        "age": 30,
        "gender": "male",
        "height_cm": 175.0,
        "weight_kg": 70.0,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 45,
        "fitness_goal": "recomp",
        "training_level": "beginner",
        "dietary_preferences": [],
    }
    values.update(overrides)
    return SaveUserOnboardingCommand(user_id=user_id, **values)


@pytest.mark.asyncio
async def test_onboarding_target_change_increments_revision_and_publishes_event():
    user_id = str(uuid4())
    profile = _profile()
    publisher = MagicMock(publish=AsyncMock())

    await SaveUserOnboardingCommandHandler(
        _uow(profile), event_publisher=publisher
    ).handle(_command(user_id, weight_kg=71.0))

    assert profile.profile_target_revision == 2
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_identical_onboarding_target_values_do_not_increment_revision():
    user_id = str(uuid4())
    profile = _profile()

    await SaveUserOnboardingCommandHandler(
        _uow(profile), event_publisher=MagicMock(publish=AsyncMock())
    ).handle(_command(user_id))

    assert profile.profile_target_revision == 1

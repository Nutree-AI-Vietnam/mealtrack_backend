"""My Plan and onboarding save a gain pace for a bulk goal."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.app.commands.user.save_user_onboarding_command import SaveUserOnboardingCommand
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.handlers.command_handlers.save_user_onboarding_command_handler import (
    SaveUserOnboardingCommandHandler,
)
from src.app.handlers.command_handlers.update_user_metrics_command_handler import (
    UpdateUserMetricsCommandHandler,
)
from src.domain.model.user.core_user import UserProfileDomainModel


def _profile(**overrides) -> UserProfileDomainModel:
    values = {
        "id": str(uuid4()),
        "user_id": "test_user",
        "age": 30,
        "gender": "male",
        "height_cm": 175.0,
        "weight_kg": 72.0,
        "job_type": "desk",
        "training_days_per_week": 0,
        "training_minutes_per_session": 0,
        "fitness_goal": "bulk",
        "meals_per_day": 3,
        "profile_target_revision": 4,
    }
    values.update(overrides)
    return UserProfileDomainModel(**values)


def _uow(profile) -> MagicMock:
    uow = MagicMock()
    uow.users.get_profile = AsyncMock(return_value=profile)
    uow.users.update_profile = AsyncMock(side_effect=lambda p: p)
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    return uow


async def _update(profile: UserProfileDomainModel, **fields) -> None:
    handler = UpdateUserMetricsCommandHandler(
        _uow(profile), event_publisher=MagicMock(publish=AsyncMock())
    )
    await handler.handle(UpdateUserMetricsCommand(user_id="test_user", **fields))


@pytest.mark.asyncio
async def test_my_plan_saves_a_gain_pace_and_starts_it_today():
    profile = _profile()

    await _update(profile, weight_gain_kg_per_week=0.5)

    assert profile.weight_gain_kg_per_week == 0.5
    assert profile.weight_loss_pace_reset_on is not None
    assert profile.profile_target_revision == 5


@pytest.mark.asyncio
async def test_my_plan_caps_a_gain_pace_only_at_the_fast_limit():
    # A desk worker's floor leaves almost no room for a loss; a gain is not
    # limited by it.
    profile = _profile()

    await _update(profile, weight_gain_kg_per_week=1.4)

    assert profile.weight_gain_kg_per_week == 1.0


@pytest.mark.asyncio
async def test_my_plan_keeps_the_day_when_the_gain_pace_is_unchanged():
    profile = _profile(weight_gain_kg_per_week=0.5)

    await _update(profile, weight_gain_kg_per_week=0.5)

    assert profile.weight_loss_pace_reset_on is None
    assert profile.profile_target_revision == 4


@pytest.mark.asyncio
async def test_my_plan_does_not_save_a_gain_pace_for_a_cut():
    profile = _profile(fitness_goal="cut")

    await _update(profile, weight_gain_kg_per_week=0.5)

    assert profile.weight_gain_kg_per_week is None
    assert profile.weight_loss_pace_reset_on is None


@pytest.mark.asyncio
async def test_my_plan_does_not_save_a_gain_pace_while_pregnant():
    profile = _profile(gender="female", health_conditions=["pregnancy"])

    await _update(profile, weight_gain_kg_per_week=0.5)

    assert profile.weight_gain_kg_per_week is None
    assert profile.weight_loss_pace_reset_on is None


@pytest.mark.asyncio
async def test_my_plan_checks_a_gain_pace_against_a_condition_sent_with_it():
    profile = _profile(gender="female")

    await _update(
        profile, health_conditions=["breastfeeding"], weight_gain_kg_per_week=0.5
    )

    assert profile.health_conditions == ["breastfeeding"]
    assert profile.weight_gain_kg_per_week is None


def _onboarding_profile() -> SimpleNamespace:
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
        training_days_per_week=0,
        training_minutes_per_session=0,
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
        weight_loss_kg_per_week=None,
        weight_gain_kg_per_week=None,
        pace_clinician_disclaimer=False,
        weight_loss_pace_reset_on=None,
        profile_target_revision=1,
    )


async def _onboard(profile: SimpleNamespace, **fields) -> None:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.find_by_id = AsyncMock(return_value=object())
    uow.users.get_profile = AsyncMock(return_value=profile)
    uow.users.update_profile = AsyncMock()
    uow.commit = AsyncMock()
    values = {
        "age": 30,
        "gender": "male",
        "height_cm": 175.0,
        "weight_kg": 70.0,
        "job_type": "desk",
        "training_days_per_week": 0,
        "training_minutes_per_session": 0,
        "fitness_goal": "bulk",
        "training_level": "beginner",
        "dietary_preferences": [],
    }
    values.update(fields)
    await SaveUserOnboardingCommandHandler(
        uow, event_publisher=MagicMock(publish=AsyncMock())
    ).handle(SaveUserOnboardingCommand(user_id=str(uuid4()), **values))


@pytest.mark.asyncio
async def test_onboarding_saves_the_full_gain_pace_with_no_floor_cap():
    profile = _onboarding_profile()

    await _onboard(profile, weight_gain_kg_per_week=1.0)

    assert profile.weight_gain_kg_per_week == 1.0
    assert profile.weight_loss_pace_reset_on is not None
    assert profile.profile_target_revision == 2


@pytest.mark.asyncio
async def test_onboarding_does_not_save_a_gain_pace_for_another_goal():
    profile = _onboarding_profile()

    await _onboard(profile, fitness_goal="recomp", weight_gain_kg_per_week=0.5)

    assert profile.weight_gain_kg_per_week is None
    assert profile.weight_loss_pace_reset_on is None


@pytest.mark.asyncio
async def test_onboarding_does_not_save_a_gain_pace_for_an_under_18():
    profile = _onboarding_profile()

    await _onboard(profile, age=16, weight_gain_kg_per_week=0.5)

    assert profile.weight_gain_kg_per_week is None

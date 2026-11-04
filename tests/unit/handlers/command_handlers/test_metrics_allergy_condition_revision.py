"""Saving allergies or conditions moves the revision that fences cached
targets and GET metrics, so both show the change at once."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.handlers.command_handlers.update_user_metrics_command_handler import (
    UpdateUserMetricsCommandHandler,
)
from src.domain.model.user.core_user import UserProfileDomainModel

SEEDED = ("milk", "egg", "fish", "shellfish", "peanut", "tree_nut", "soy", "wheat")


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
        "fitness_goal": "cut",
        "meals_per_day": 3,
        "profile_target_revision": 4,
    }
    values.update(overrides)
    return UserProfileDomainModel(**values)


async def _update(profile: UserProfileDomainModel, **fields) -> None:
    uow = MagicMock()
    uow.users.get_profile = AsyncMock(return_value=profile)
    uow.users.update_profile = AsyncMock(side_effect=lambda p: p)
    uow.catalog_recipes.list_allergen_codes = AsyncMock(return_value=list(SEEDED))
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    handler = UpdateUserMetricsCommandHandler(
        uow, event_publisher=MagicMock(publish=AsyncMock())
    )
    await handler.handle(UpdateUserMetricsCommand(user_id="test_user", **fields))


@pytest.mark.asyncio
async def test_a_new_condition_bumps_the_revision():
    """Pregnancy blocks the deficit; a cached target must not outlive it."""
    profile = _profile()

    await _update(profile, health_conditions=["pregnancy"])

    assert profile.health_conditions == ["pregnancy"]
    assert profile.profile_target_revision == 5


@pytest.mark.asyncio
async def test_a_new_allergy_bumps_the_revision():
    profile = _profile()

    await _update(profile, allergies=["tôm"])

    assert profile.allergies == ["tôm"]
    assert profile.profile_target_revision == 5


@pytest.mark.asyncio
async def test_clearing_allergies_bumps_the_revision():
    profile = _profile(allergies=["tôm"])

    await _update(profile, allergies=[])

    assert profile.allergies == []
    assert profile.profile_target_revision == 5


@pytest.mark.asyncio
async def test_reordered_or_recased_lists_keep_the_revision():
    profile = _profile(
        health_conditions=["pregnancy", "kidney_disease"], allergies=["tôm", "milk"]
    )

    await _update(
        profile,
        health_conditions=["Kidney disease", "pregnancy"],
        allergies=["milk", "TÔM"],
    )

    assert profile.profile_target_revision == 4

"""My Plan sets the date of birth; older apps still send the age alone."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.handlers.command_handlers.update_user_metrics_command_handler import (
    UpdateUserMetricsCommandHandler,
)
from src.domain.model.user.core_user import UserProfileDomainModel

_HANDLER = "src.app.handlers.command_handlers.update_user_metrics_command_handler"


class _Today(date):
    """2026-06-15, so ages are exact."""

    @classmethod
    def today(cls):
        return cls(2026, 6, 15)


@pytest.fixture(autouse=True)
def _fixed_today():
    with patch(f"{_HANDLER}.date", _Today):
        yield


def _profile(**overrides) -> UserProfileDomainModel:
    values = {
        "id": str(uuid4()),
        "user_id": "test_user",
        "age": 25,
        "gender": "female",
        "height_cm": 160.0,
        "weight_kg": 55.0,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 45,
        "fitness_goal": "recomp",
        "meals_per_day": 3,
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


async def _update(profile: UserProfileDomainModel, **fields) -> MagicMock:
    uow = _uow(profile)
    handler = UpdateUserMetricsCommandHandler(
        uow, event_publisher=MagicMock(publish=AsyncMock())
    )
    await handler.handle(UpdateUserMetricsCommand(user_id="test_user", **fields))
    return uow


@pytest.mark.asyncio
async def test_date_of_birth_is_stored_and_sets_the_age():
    profile = _profile()

    uow = await _update(profile, date_of_birth=date(1996, 3, 1))

    assert profile.date_of_birth == date(1996, 3, 1)
    assert profile.age == 30
    assert profile.profile_target_revision == 5
    uow.users.update_profile.assert_awaited_once_with(profile)


@pytest.mark.asyncio
async def test_age_counts_the_birthday_only_once_it_has_passed():
    profile = _profile()

    await _update(profile, date_of_birth=date(1996, 6, 16))

    assert profile.age == 29


@pytest.mark.asyncio
async def test_date_of_birth_wins_over_an_age_sent_with_it():
    profile = _profile()

    await _update(profile, date_of_birth=date(1996, 3, 1), age=40)

    assert profile.age == 30
    assert profile.date_of_birth == date(1996, 3, 1)


@pytest.mark.asyncio
async def test_new_date_with_the_same_age_bumps_the_revision():
    """GET metrics' cache is fenced by the revision, so it must move."""
    profile = _profile(age=30, date_of_birth=date(1996, 3, 1))

    await _update(profile, date_of_birth=date(1996, 4, 1))

    assert profile.age == 30
    assert profile.date_of_birth == date(1996, 4, 1)
    assert profile.profile_target_revision == 5


@pytest.mark.asyncio
async def test_age_only_update_that_disagrees_clears_the_stored_date():
    profile = _profile(age=30, date_of_birth=date(1996, 3, 1))

    await _update(profile, age=31)

    assert profile.age == 31
    assert profile.date_of_birth is None
    assert profile.profile_target_revision == 5


@pytest.mark.asyncio
async def test_age_only_update_that_matches_keeps_the_stored_date():
    profile = _profile(age=30, date_of_birth=date(1996, 3, 1))

    await _update(profile, age=30)

    assert profile.age == 30
    assert profile.date_of_birth == date(1996, 3, 1)
    assert profile.profile_target_revision == 4


@pytest.mark.asyncio
async def test_age_only_update_echoing_a_stale_stored_age_keeps_the_date():
    """Older apps resend the stored age on every My Plan save; after a
    birthday it trails the date, which must survive."""
    profile = _profile(age=29, date_of_birth=date(1996, 3, 1))

    await _update(profile, age=29, weight_kg=56.0)

    assert profile.date_of_birth == date(1996, 3, 1)
    assert profile.age == 29


@pytest.mark.asyncio
async def test_age_changed_to_the_one_the_date_gives_keeps_the_date():
    profile = _profile(age=29, date_of_birth=date(1996, 3, 1))

    await _update(profile, age=30)

    assert profile.date_of_birth == date(1996, 3, 1)
    assert profile.age == 30


@pytest.mark.asyncio
async def test_age_only_update_without_a_stored_date_just_sets_the_age():
    profile = _profile(age=30)

    await _update(profile, age=33)

    assert profile.age == 33
    assert profile.date_of_birth is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "date_of_birth",
    [date(2026, 6, 16), date(2015, 1, 1), date(1905, 1, 1)],
    ids=["future", "under-12", "over-120"],
)
async def test_handler_refuses_a_date_outside_the_age_bounds(date_of_birth):
    profile = _profile(age=30, date_of_birth=date(1996, 3, 1))

    with pytest.raises(ValidationException):
        await _update(profile, date_of_birth=date_of_birth)

    assert profile.date_of_birth == date(1996, 3, 1)
    assert profile.age == 30

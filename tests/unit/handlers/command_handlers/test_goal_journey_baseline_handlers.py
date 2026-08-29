from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.app.commands.user import SaveUserOnboardingCommand
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.handlers.command_handlers.save_user_onboarding_command_handler import (
    SaveUserOnboardingCommandHandler,
)
from src.app.handlers.command_handlers.update_user_metrics_command_handler import (
    UpdateUserMetricsCommandHandler,
)


class _FakeUsers:
    def __init__(self):
        self.saved_profile = None

    async def find_by_id(self, user_id):
        return object()

    async def get_profile(self, user_id):
        return None

    async def update_profile(self, profile):
        self.saved_profile = profile


class _FakeUow:
    def __init__(self):
        self.users = _FakeUsers()
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class _FakeMetricUsers:
    def __init__(self, profile):
        self.profile = profile
        self.saved_profile = None

    async def get_profile(self, user_id):
        return self.profile

    async def update_profile(self, profile):
        self.saved_profile = profile


class _FakeMetricUow:
    def __init__(self, profile):
        self.users = _FakeMetricUsers(profile)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.mark.asyncio
async def test_onboarding_sets_goal_journey_baseline(monkeypatch):
    fixed_now = datetime(2026, 6, 21, 1, 2, 3, tzinfo=UTC)
    monkeypatch.setattr(
        "src.app.handlers.command_handlers.save_user_onboarding_command_handler.utc_now",
        lambda: fixed_now,
    )
    uow = _FakeUow()
    handler = SaveUserOnboardingCommandHandler(
        uow=uow, event_publisher=MagicMock(publish=AsyncMock())
    )
    user_id = str(UUID("11111111-1111-1111-1111-111111111111"))

    await handler.handle(
        SaveUserOnboardingCommand(
            user_id=user_id,
            age=30,
            gender="male",
            height_cm=180,
            weight_kg=80,
            target_weight_kg=75,
            job_type="desk",
            training_days_per_week=3,
            training_minutes_per_session=45,
            fitness_goal="cut",
        )
    )

    assert uow.committed is True
    assert uow.users.saved_profile.goal_start_weight_kg == 80
    assert uow.users.saved_profile.goal_started_at == fixed_now


@pytest.mark.asyncio
async def test_target_weight_change_resets_journey_seed(monkeypatch):
    fixed_now = datetime(2026, 6, 24, 1, 2, 3, tzinfo=UTC)
    monkeypatch.setattr(
        "src.app.handlers.command_handlers.update_user_metrics_command_handler.utc_now",
        lambda: fixed_now,
    )

    profile = type(
        "Profile",
        (),
        {
            "age": 30,
            "height_cm": 180.0,
            "gender": "male",
            "job_type": "desk",
            "training_days_per_week": 3,
            "training_minutes_per_session": 45,
            "fitness_goal": "cut",
            "training_level": "beginner",
            "target_weight_kg": 75.0,
            "goal_started_at": datetime(2026, 6, 1, tzinfo=UTC),
            "goal_start_weight_kg": 80.0,
            "journey_progress_seed_percent": 50.0,
            "profile_target_revision": 1,
            "weight_kg": 78.0,
            "is_current": True,
        },
    )()
    uow = _FakeMetricUow(profile)
    handler = UpdateUserMetricsCommandHandler(
        uow=uow, event_publisher=MagicMock(publish=AsyncMock())
    )

    await handler.handle(
        UpdateUserMetricsCommand(
            user_id=str(UUID("11111111-1111-1111-1111-111111111111")),
            target_weight_kg=72.0,
        )
    )

    assert uow.users.saved_profile.target_weight_kg == 72.0
    assert uow.users.saved_profile.goal_start_weight_kg == 78.0
    assert uow.users.saved_profile.goal_started_at == fixed_now
    assert uow.users.saved_profile.journey_progress_seed_percent == 0.0

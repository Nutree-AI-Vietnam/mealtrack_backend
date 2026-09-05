"""
Unit tests for update user metrics endpoint and handler.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.user.update_user_metrics_command import UpdateUserMetricsCommand
from src.app.handlers.command_handlers.update_user_metrics_command_handler import (
    UpdateUserMetricsCommandHandler,
)
from src.domain.model.user.body_fat_visual import BodyFatVisualProfileSelection
from src.domain.model.user.core_user import UserProfileDomainModel


def _make_profile(**overrides) -> UserProfileDomainModel:
    """Create a domain profile with sensible defaults."""
    defaults = {
        "id": str(uuid4()),
        "user_id": "test_user",
        "age": 30,
        "gender": "male",
        "height_cm": 175.0,
        "weight_kg": 70.0,
        "job_type": "desk",
        "training_days_per_week": 4,
        "training_minutes_per_session": 60,
        "fitness_goal": "recomp",
        "meals_per_day": 3,
        "is_current": False,
    }
    defaults.update(overrides)
    return UserProfileDomainModel(**defaults)


def _make_mock_uow(profile=None):
    """Create a mock UoW that returns the given profile from users.get_profile()."""
    mock_uow = MagicMock()
    mock_uow.users.get_profile = AsyncMock(return_value=profile)
    mock_uow.users.update_profile = AsyncMock(side_effect=lambda p: p)
    mock_uow.body_fat_visual_profiles.find_history_by_user = AsyncMock(return_value=[])
    mock_uow.body_fat_visual_profiles.append = AsyncMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    return mock_uow


def _publisher():
    return MagicMock(publish=AsyncMock())


class TestUpdateUserMetricsCommand:
    """Test UpdateUserMetricsCommand data class."""

    def test_create_command_with_all_fields(self):
        """Test creating command with all metrics."""
        command = UpdateUserMetricsCommand(
            user_id="test_user",
            age=35,
            height_cm=180.0,
            weight_kg=75.0,
            biological_sex="female",
            job_type="desk",
            training_days_per_week=4,
            training_minutes_per_session=60,
            body_fat_percent=15.0,
            fitness_goal="cut",
            target_weight_kg=70.0,
        )

        assert command.user_id == "test_user"
        assert command.age == 35
        assert command.height_cm == 180.0
        assert command.weight_kg == 75.0
        assert command.biological_sex == "female"
        assert command.job_type == "desk"
        assert command.training_days_per_week == 4
        assert command.training_minutes_per_session == 60
        assert command.body_fat_percent == 15.0
        assert command.fitness_goal == "cut"
        assert command.target_weight_kg == 70.0

    def test_create_command_with_partial_fields(self):
        """Test creating command with only some metrics."""
        command = UpdateUserMetricsCommand(user_id="test_user", weight_kg=75.0)

        assert command.user_id == "test_user"
        assert command.weight_kg == 75.0
        assert command.job_type is None
        assert command.training_days_per_week is None
        assert command.training_minutes_per_session is None
        assert command.body_fat_percent is None
        assert command.fitness_goal is None
        assert command.target_weight_kg is None


@pytest.mark.asyncio
class TestUpdateUserMetricsCommandHandler:
    """Test UpdateUserMetricsCommandHandler."""

    async def test_update_weight_only(self):
        """Test updating only weight."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(user_id="test_user", weight_kg=75.0)

        await handler.handle(command)

        # Verify profile was mutated and update_profile was called
        assert profile.weight_kg == 75.0
        assert profile.is_current is True
        mock_uow.users.update_profile.assert_called_once_with(profile)

    async def test_target_change_increments_revision_once_and_cache_failure_is_non_fatal(
        self,
    ):
        profile = _make_profile(profile_target_revision=1)
        mock_uow = _make_mock_uow(profile)
        cache = MagicMock()
        cache.invalidate = AsyncMock(side_effect=RuntimeError("redis unavailable"))

        await UpdateUserMetricsCommandHandler(
            mock_uow, event_publisher=_publisher()
        ).handle(UpdateUserMetricsCommand(user_id="test_user", weight_kg=75.0))

        assert profile.profile_target_revision == 2
        mock_uow.users.update_profile.assert_awaited_once_with(profile)

    async def test_identical_target_input_does_not_increment_revision(self):
        profile = _make_profile(profile_target_revision=1)
        mock_uow = _make_mock_uow(profile)

        await UpdateUserMetricsCommandHandler(
            mock_uow, event_publisher=_publisher()
        ).handle(UpdateUserMetricsCommand(user_id="test_user", weight_kg=70.0))

        assert profile.profile_target_revision == 1

    async def test_update_job_type_and_training_only(self):
        """Test updating only job type and training."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(
            user_id="test_user",
            job_type="on_feet",
            training_days_per_week=5,
            training_minutes_per_session=60,
        )

        await handler.handle(command)

        assert profile.job_type == "on_feet"
        assert profile.training_days_per_week == 5
        assert profile.training_minutes_per_session == 60
        assert profile.is_current is True

    async def test_update_training_days_to_zero(self):
        """Test zero training days is treated as a provided metric."""
        profile = _make_profile(training_days_per_week=4)
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(
            user_id="test_user",
            training_days_per_week=0,
        )

        await handler.handle(command)

        assert profile.training_days_per_week == 0
        assert profile.training_minutes_per_session == 0
        assert profile.is_current is True
        mock_uow.users.update_profile.assert_called_once_with(profile)

    async def test_no_training_normalizes_a_supplied_session_duration(self):
        profile = _make_profile(
            training_days_per_week=4, training_minutes_per_session=60
        )
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(
            user_id="test_user",
            training_days_per_week=0,
            training_minutes_per_session=60,
        )

        await handler.handle(command)

        assert profile.training_days_per_week == 0
        assert profile.training_minutes_per_session == 0

    async def test_update_fitness_goal_unlimited(self):
        """Test updating fitness goal succeeds without cooldown."""
        profile = _make_profile(fitness_goal="recomp", is_current=True)
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(user_id="test_user", fitness_goal="cut")

        await handler.handle(command)

        assert profile.fitness_goal == "cut"
        assert profile.is_current is True
        mock_uow.users.update_profile.assert_called_once()

    async def test_update_all_metrics_together(self):
        """Test updating all metrics in one call."""
        profile = _make_profile(body_fat_percentage=20.0)
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(
            user_id="test_user",
            age=35,
            height_cm=180.0,
            weight_kg=72.5,
            biological_sex="female",
            job_type="on_feet",
            training_days_per_week=5,
            training_minutes_per_session=60,
            body_fat_percent=15.0,
            fitness_goal="cut",
        )

        await handler.handle(command)

        assert profile.age == 35
        assert profile.height_cm == 180.0
        assert profile.weight_kg == 72.5
        assert profile.gender == "female"
        assert profile.job_type == "on_feet"
        assert profile.training_days_per_week == 5
        assert profile.training_minutes_per_session == 60
        assert profile.body_fat_percentage == 15.0
        assert profile.fitness_goal == "cut"
        assert profile.is_current is True

    async def test_sex_change_appends_latest_visual_profile_remapped_by_ordinal(self):
        profile = _make_profile(gender="male")
        visual_profile = BodyFatVisualProfileSelection(
            user_id="test_user",
            schema_version=1,
            range_catalog_version=1,
            sex_at_selection="male",
            start_range_id="male_13_16",
            current_range_id="male_17_20",
            target_range_id=None,
        )
        mock_uow = _make_mock_uow(profile)
        mock_uow.body_fat_visual_profiles.find_history_by_user = AsyncMock(
            return_value=[visual_profile]
        )

        await UpdateUserMetricsCommandHandler(
            mock_uow, event_publisher=_publisher()
        ).handle(UpdateUserMetricsCommand(user_id="test_user", biological_sex="female"))

        remapped = mock_uow.body_fat_visual_profiles.append.await_args.args[0]
        assert profile.gender == "female"
        assert remapped.sex_at_selection == "female"
        assert remapped.start_range_id == "female_22_25"
        assert remapped.current_range_id == "female_26_30"
        assert remapped.target_range_id is None
        mock_uow.users.update_profile.assert_awaited_once_with(profile)

    async def test_unchanged_sex_or_missing_visual_history_does_not_append(self):
        profile = _make_profile(gender="male")
        mock_uow = _make_mock_uow(profile)
        handler = UpdateUserMetricsCommandHandler(
            mock_uow, event_publisher=_publisher()
        )

        await handler.handle(
            UpdateUserMetricsCommand(user_id="test_user", biological_sex="male")
        )
        mock_uow.body_fat_visual_profiles.find_history_by_user.assert_not_awaited()
        mock_uow.body_fat_visual_profiles.append.assert_not_awaited()

        await handler.handle(
            UpdateUserMetricsCommand(user_id="test_user", biological_sex="female")
        )
        mock_uow.body_fat_visual_profiles.find_history_by_user.assert_awaited_once_with(
            "test_user"
        )
        mock_uow.body_fat_visual_profiles.append.assert_not_awaited()

    async def test_clear_body_fat_when_explicitly_requested(self):
        """Test clearing body fat requires an explicit clear signal."""
        profile = _make_profile(body_fat_percentage=20.0)
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(
            user_id="test_user",
            body_fat_percent=None,
            body_fat_percent_provided=True,
        )

        await handler.handle(command)

        assert profile.body_fat_percentage is None
        assert profile.is_current is True

    async def test_user_not_found(self):
        """Test error when user profile doesn't exist."""
        mock_uow = _make_mock_uow(profile=None)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(user_id="nonexistent_user", weight_kg=75.0)

        with pytest.raises(ResourceNotFoundException) as exc_info:
            await handler.handle(command)

        assert "nonexistent_user" in str(exc_info.value)

    async def test_no_metrics_provided(self):
        """Test error when no metrics are provided."""
        mock_uow = _make_mock_uow()

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(user_id="test_user")

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "At least one metric must be provided" in str(exc_info.value)

    async def test_invalid_weight(self):
        """Test validation for invalid weight."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(user_id="test_user", weight_kg=-5.0)

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Weight must be greater than 0" in str(exc_info.value)

    async def test_invalid_age(self):
        """Test validation for invalid age."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(uow=mock_uow)
        command = UpdateUserMetricsCommand(user_id="test_user", age=11)

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Age must be between 12 and 120" in str(exc_info.value)

    async def test_invalid_height(self):
        """Test validation for invalid height."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(uow=mock_uow)
        command = UpdateUserMetricsCommand(user_id="test_user", height_cm=99.0)

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Height must be between 100 and 272 cm" in str(exc_info.value)

    async def test_invalid_biological_sex(self):
        """Test validation for invalid biological sex."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(uow=mock_uow)
        command = UpdateUserMetricsCommand(
            user_id="test_user", biological_sex="unknown"
        )

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Biological sex must be male or female" in str(exc_info.value)

    async def test_invalid_body_fat(self):
        """Test validation for body fat out of range."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(uow=mock_uow)
        command = UpdateUserMetricsCommand(user_id="test_user", body_fat_percent=75.0)

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Body fat percentage must be between 0 and 70" in str(exc_info.value)

    async def test_invalid_job_type(self):
        """Test validation for invalid job type."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(uow=mock_uow)
        command = UpdateUserMetricsCommand(user_id="test_user", job_type="invalid_job")

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Job type must be one of" in str(exc_info.value)

    async def test_invalid_fitness_goal(self):
        """Test validation for invalid fitness goal."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(uow=mock_uow)
        command = UpdateUserMetricsCommand(
            user_id="test_user", fitness_goal="super_shredded"
        )

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Fitness goal must be one of" in str(exc_info.value)

    async def test_update_target_weight(self):
        """Test updating target weight."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(
            uow=mock_uow, event_publisher=_publisher()
        )
        command = UpdateUserMetricsCommand(user_id="test_user", target_weight_kg=65.0)

        await handler.handle(command)

        assert profile.target_weight_kg == 65.0
        assert profile.is_current is True
        mock_uow.users.update_profile.assert_called_once_with(profile)

    async def test_invalid_target_weight(self):
        """Test validation for invalid target weight."""
        profile = _make_profile()
        mock_uow = _make_mock_uow(profile)

        handler = UpdateUserMetricsCommandHandler(uow=mock_uow)
        command = UpdateUserMetricsCommand(user_id="test_user", target_weight_kg=-10.0)

        with pytest.raises(ValidationException) as exc_info:
            await handler.handle(command)

        assert "Target weight must be greater than 0" in str(exc_info.value)

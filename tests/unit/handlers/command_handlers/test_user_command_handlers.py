"""
Unit tests for user command handlers.
"""

import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
from tests.conftest import TestUnitOfWork, TestUserRepository

from src.api.exceptions import ValidationException
from src.app.commands.user import SaveUserOnboardingCommand


@pytest.mark.unit
class TestSaveUserOnboardingCommandHandler:
    """Test SaveUserOnboardingCommand handler."""

    @pytest.mark.asyncio
    async def test_save_user_onboarding_success(self, event_bus, test_session):
        """Test successful user onboarding save."""
        # Create user first with unique IDs
        from uuid import UUID

        user_id = str(uuid.uuid4())
        unique_suffix = str(uuid.uuid4())[:8]  # Use only first 8 chars
        firebase_uid = f"test-fb-{unique_suffix}"

        from src.infra.database.models.user.user import User

        user = User(
            id=user_id,  # String ID as per BaseMixin
            firebase_uid=firebase_uid,
            email=f"test-{unique_suffix}@example.com",
            username=f"user-{unique_suffix}",
            password_hash="dummy_hash",
            is_active=True,  # Required for find_by_id to work
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        # Add user to session and commit
        test_session.add(user)
        test_session.flush()
        test_session.commit()

        # Verify user is queryable directly (not via repository) to ensure it's in the DB
        db_user = (
            test_session.query(User)
            .filter(User.id == user_id, User.is_active.is_(True))
            .first()
        )
        assert (
            db_user is not None
        ), f"User {user_id} must be queryable directly in test_session"

        # Verify repository can find user with UUID conversion
        test_repo = TestUserRepository(test_session)
        repo_user = test_repo.find_by_id(UUID(user_id))
        assert (
            repo_user is not None
        ), f"UserRepository must find user {user_id} with UUID conversion"

        # Arrange
        command = SaveUserOnboardingCommand(
            user_id=user_id,
            age=30,
            gender="male",
            height_cm=175,
            weight_kg=70,
            job_type="desk",
            training_days_per_week=4,
            training_minutes_per_session=60,
            fitness_goal="recomp",
            dietary_preferences=["vegetarian"],
            pain_points=["diabetes"],
            referral_sources=["tiktok", "friend_family"],
        )

        # Act - Get handler from event_bus and set its uow to use test_session
        # The repository can find the user, so UnitOfWork should too
        from src.app.commands.user.save_user_onboarding_command import (
            SaveUserOnboardingCommand as CmdType,
        )

        handler = event_bus._async_handlers.get(CmdType)
        if handler:
            original_uow = handler.uow
            # Create UoW with test_session - repository verified it can find user
            test_uow = TestUnitOfWork(session=test_session)
            handler.uow = test_uow
            try:
                result = await event_bus.send(command)
            finally:
                handler.uow = original_uow
        else:
            # Fallback: patch UnitOfWork
            with patch(
                "src.app.handlers.command_handlers.save_user_onboarding_command_handler.AsyncUnitOfWork",
                side_effect=lambda *args, **kwargs: TestUnitOfWork(
                    session=test_session
                ),
            ):
                result = await event_bus.send(command)

        # Assert - SaveUserOnboardingCommand should return None
        assert result is None

        # Verify the profile was created/updated in the database
        from src.infra.database.models.user.profile import UserProfile

        saved_profile = (
            test_session.query(UserProfile)
            .filter(UserProfile.user_id == user_id)
            .first()
        )

        assert saved_profile is not None
        assert saved_profile.age == 30
        assert saved_profile.gender == "male"
        assert saved_profile.height_cm == 175
        assert saved_profile.weight_kg == 70
        assert saved_profile.job_type == "desk"
        assert saved_profile.training_days_per_week == 4
        assert saved_profile.training_minutes_per_session == 60
        assert saved_profile.fitness_goal == "recomp"

    @pytest.mark.asyncio
    async def test_save_user_onboarding_invalid_age(self, event_bus, test_session):
        """Test onboarding with invalid age."""
        # Create user first with unique IDs
        user_id = str(uuid.uuid4())
        unique_suffix = str(uuid.uuid4())[:8]  # Use only first 8 chars
        firebase_uid = f"test-fb-{unique_suffix}"

        from src.infra.database.models.user.user import User

        user = User(
            id=user_id,
            firebase_uid=firebase_uid,
            email=f"test-{unique_suffix}@example.com",
            username=f"user-{unique_suffix}",
            password_hash="dummy_hash",
            is_active=True,  # Required for find_by_id to work
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        test_session.add(user)
        test_session.commit()

        # Arrange
        command = SaveUserOnboardingCommand(
            user_id=user_id,
            age=-5,  # Invalid age
            gender="male",
            height_cm=175,
            weight_kg=70,
            job_type="desk",
            training_days_per_week=4,
            training_minutes_per_session=60,
            fitness_goal="maintenance",
            dietary_preferences=[],
            pain_points=[],
            referral_sources=["google_search"],
        )

        # Act & Assert
        with pytest.raises(ValidationException):
            await event_bus.send(command)

    @pytest.mark.asyncio
    async def test_save_user_onboarding_invalid_weight(self, event_bus, test_session):
        """Test onboarding with invalid weight."""
        # Create user first with unique IDs
        user_id = str(uuid.uuid4())
        unique_suffix = str(uuid.uuid4())[:8]  # Use only first 8 chars
        firebase_uid = f"test-fb-{unique_suffix}"

        from src.infra.database.models.user.user import User

        user = User(
            id=user_id,
            firebase_uid=firebase_uid,
            email=f"test-{unique_suffix}@example.com",
            username=f"user-{unique_suffix}",
            password_hash="dummy_hash",
            is_active=True,  # Required for find_by_id to work
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        test_session.add(user)
        test_session.commit()

        # Arrange
        command = SaveUserOnboardingCommand(
            user_id=user_id,
            age=30,
            gender="male",
            height_cm=175,
            weight_kg=0,  # Invalid weight
            job_type="desk",
            training_days_per_week=4,
            training_minutes_per_session=60,
            fitness_goal="maintenance",
            dietary_preferences=[],
            pain_points=[],
            referral_sources=["instagram"],
        )

        # Act & Assert
        with pytest.raises(ValidationException):
            await event_bus.send(command)

    @pytest.mark.asyncio
    async def test_save_user_onboarding_updates_existing_profile(
        self, event_bus, test_session, sample_user_profile
    ):
        """Test updating existing user profile."""
        # Arrange
        user_id = sample_user_profile.user_id
        command = SaveUserOnboardingCommand(
            user_id=user_id,
            age=35,  # Different age
            gender="male",
            height_cm=180,  # Different height
            weight_kg=75,  # Different weight
            job_type="on_feet",
            training_days_per_week=5,
            training_minutes_per_session=60,  # Different activity
            fitness_goal="cut",  # Different goal
            dietary_preferences=["vegan"],
            pain_points=[],
            referral_sources=["youtube"],
        )

        # Act - Get handler from event_bus and set its uow to use test_session
        # The handler uses 'self.uow or UnitOfWork()', so if we set self.uow, it will use that
        from src.app.commands.user.save_user_onboarding_command import (
            SaveUserOnboardingCommand as CmdType,
        )

        handler = event_bus._async_handlers.get(CmdType)
        if handler:
            original_uow = handler.uow
            test_uow = TestUnitOfWork(session=test_session)
            handler.uow = test_uow
            try:
                result = await event_bus.send(command)
            finally:
                handler.uow = original_uow
        else:
            # Fallback: patch UnitOfWork
            with patch(
                "src.app.handlers.command_handlers.save_user_onboarding_command_handler.AsyncUnitOfWork",
                side_effect=lambda *args, **kwargs: TestUnitOfWork(
                    session=test_session
                ),
            ):
                result = await event_bus.send(command)

        # Assert - SaveUserOnboardingCommand should return None
        assert result is None
        # Verify profile was updated
        # Use user_id string directly to avoid DetachedInstanceError
        from src.infra.database.models.user.profile import UserProfile

        updated_profile = (
            test_session.query(UserProfile)
            .filter(UserProfile.user_id == user_id)
            .first()
        )
        assert updated_profile is not None, f"Profile should exist for user {user_id}"
        assert updated_profile.age == 35
        assert updated_profile.height_cm == 180
        assert updated_profile.weight_kg == 75

"""
Unit tests for SyncUserCommandHandler.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.user.sync_user_command import SyncUserCommand
from src.app.handlers.command_handlers.sync_user_command_handler import (
    SyncUserCommandHandler,
)
from src.domain.exceptions.firebase_identity_exceptions import (
    FirebaseIdentityConflictError,
)


@pytest.fixture
def handler():
    """Create a SyncUserCommandHandler instance."""
    return SyncUserCommandHandler()


class TestSyncUserCommandHandler:
    """Test suite for SyncUserCommandHandler."""

    @pytest.mark.asyncio
    async def test_handle_create_new_user(self, handler):
        """Test creating a new user when no user exists."""
        fake_uow = FakeUnitOfWork()
        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_123",
            email="newuser@example.com",
            phone_number="+1234567890",
            display_name="New User",
            photo_url="https://example.com/photo.jpg",
            provider="google",
            username="newuser",
            first_name="New",
            last_name="User",
        )

        result = await handler.handle(command)

        assert result["created"] is True
        assert result["updated"] is False
        assert result["user"]["firebase_uid"] == "firebase_123"
        assert result["user"]["email"] == "newuser@example.com"
        assert result["message"] == "User created successfully"
        assert fake_uow.committed is True

    @pytest.mark.asyncio
    async def test_new_user_sync_uses_one_lookup_and_one_save(self, handler):
        """New-user sync must not repeat the Firebase lookup or the language write.

        PYTHON-JY: POST /v1/users/sync issued a separate active lookup and a
        deleted lookup, saved the user, then saved again to set language_code.
        """
        fake_uow = FakeUnitOfWork()
        users = fake_uow.users
        calls: list[str] = []
        languages_at_save: list[str | None] = []

        original_active = users.find_by_firebase_uid
        original_any = users.find_by_firebase_uid_including_deleted
        original_save = users.save

        async def find_active(firebase_uid: str):
            calls.append("active")
            return await original_active(firebase_uid)

        async def find_any(firebase_uid: str):
            calls.append("including_deleted")
            return await original_any(firebase_uid)

        async def save(user):
            calls.append("save")
            languages_at_save.append(getattr(user, "language_code", None))
            return await original_save(user)

        users.find_by_firebase_uid = find_active
        users.find_by_firebase_uid_including_deleted = find_any
        users.save = save

        handler.uow = fake_uow
        result = await handler.handle(
            SyncUserCommand(
                firebase_uid="firebase_new_lang",
                email="new-lang@example.com",
                display_name="New Lang",
                provider="google",
                language_code="vi",
            )
        )

        identity_lookups = [
            call for call in calls if call in {"active", "including_deleted"}
        ]
        assert identity_lookups == ["including_deleted"]
        assert calls.count("save") == 1
        assert languages_at_save == ["vi"]
        assert result["created"] is True
        assert result["updated"] is False
        assert result["message"] == "User created successfully"
        assert result["user"]["firebase_uid"] == "firebase_new_lang"
        assert result["user"]["email"] == "new-lang@example.com"
        assert result["user"]["has_subscription"] is False
        assert fake_uow.committed is True

    @pytest.mark.asyncio
    async def test_handle_update_existing_user(self, handler):
        """Test updating an existing user."""
        fake_uow = FakeUnitOfWork()
        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        # Pre-populate existing user
        existing_user = UserDomainModel(
            firebase_uid="firebase_456",
            email="old@example.com",
            username="olduser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
        )
        await fake_uow.users.save(existing_user)

        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_456",
            email="updated@example.com",
            phone_number="+1987654321",
            display_name="Updated User",
            photo_url="https://example.com/newphoto.jpg",
            provider="google",
            username="updateduser",
            first_name="Updated",
            last_name="User",
        )

        result = await handler.handle(command)

        assert result["created"] is False
        assert result["updated"] is True
        assert result["message"] == "User updated successfully"
        assert fake_uow.committed is True

    @pytest.mark.asyncio
    async def test_handle_rejects_email_collision_with_different_firebase_uid(self, handler):
        """An email lookup must never move an existing account to a new UID."""
        fake_uow = FakeUnitOfWork()
        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        existing_user = UserDomainModel(
            firebase_uid="firebase_original",
            email="existing@example.com",
            username="existinguser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
        )
        await fake_uow.users.save(existing_user)
        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_untrusted",
            email="existing@example.com",
            provider="google",
        )

        with pytest.raises(FirebaseIdentityConflictError):
            await handler.handle(command)

        assert existing_user.firebase_uid == "firebase_original"
        assert fake_uow.rolled_back is True

    @pytest.mark.asyncio
    async def test_handle_propagates_persistence_identity_conflict(self, handler):
        """A unique-index race must retain the generic identity-conflict path."""
        fake_uow = FakeUnitOfWork()
        fake_uow.users.save = AsyncMock(
            side_effect=FirebaseIdentityConflictError("unique-index race")
        )
        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_retry",
            email="retry@example.com",
            provider="google",
        )

        with pytest.raises(FirebaseIdentityConflictError):
            await handler.handle(command)

        assert fake_uow.rolled_back is True

    @pytest.mark.asyncio
    async def test_handle_no_changes(self, handler):
        """Test when user exists but no updates needed."""
        fake_uow = FakeUnitOfWork()
        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        # Pre-populate existing user
        existing_user = UserDomainModel(
            firebase_uid="firebase_789",
            email="same@example.com",
            username="sameuser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
        )
        await fake_uow.users.save(existing_user)

        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_789",
            email="same@example.com",
            phone_number="+1234567890",
            display_name="Same User",
            photo_url="https://example.com/photo.jpg",
            provider="google",
        )

        result = await handler.handle(command)

        assert result["created"] is False
        assert result["message"] in [
            "User updated successfully",
            "User data up to date",
        ]
        assert fake_uow.committed is True

    @pytest.mark.asyncio
    async def test_handle_with_standard_subscription(self, handler):
        """Test syncing user with active standard subscription."""
        fake_uow = FakeUnitOfWork()
        # Pre-populate user with standard subscription
        from uuid import uuid4

        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        user_id = uuid4()
        existing_user = UserDomainModel(
            id=user_id,  # Set ID explicitly
            firebase_uid="firebase_standard",
            email="standard@example.com",
            username="standarduser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
        )
        await fake_uow.users.save(existing_user)

        # Add standard subscription
        # Use future datetime that's definitely in the future
        from datetime import timedelta

        from src.domain.model.subscription import Subscription

        expires_at = datetime.now() + timedelta(days=365)  # 1 year in the future
        subscription = Subscription(
            user_id=str(user_id),  # Use the same user_id
            product_id="standard_monthly",
            status="active",
            expires_at=expires_at,
            platform="ios",
        )
        saved_subscription = await fake_uow.subscriptions.save(subscription)

        # Verify subscription is saved correctly
        assert saved_subscription.id is not None
        assert saved_subscription.user_id == str(user_id)

        # Debug: Check all subscriptions
        all_subs = list(fake_uow.subscriptions._subscriptions.values())
        matching = [s for s in all_subs if s.user_id == str(user_id)]
        assert (
            len(matching) > 0
        ), f"Should have subscription for user {user_id}. All: {[(s.user_id, s.status) for s in all_subs]}"

        # Verify subscription can be found before handler runs
        found_before = await fake_uow.subscriptions.find_active_by_user_id(str(user_id))
        if found_before is None:
            # Debug datetime comparison
            for sub in matching:
                if sub.expires_at:
                    from datetime import datetime as dt

                    is_valid = sub.expires_at > dt.now()
                    print(
                        f"Subscription expires_at={sub.expires_at}, now={dt.now()}, valid={is_valid}"
                    )
        assert (
            found_before is not None
        ), f"Subscription should be findable for user {user_id} before handler runs"

        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_standard",
            email="standard@example.com",
            provider="google",
        )

        result = await handler.handle(command)

        # Verify user ID matches - handler should find existing user
        result_user_id = result["user"]["id"]
        assert str(result_user_id) == str(
            user_id
        ), f"User ID mismatch: {result_user_id} != {user_id}"

        # Verify subscription can still be found after handler runs
        found_after = await fake_uow.subscriptions.find_active_by_user_id(
            str(result_user_id)
        )
        assert (
            found_after is not None
        ), f"Subscription should still be findable for user {result_user_id} after handler runs"

        # The handler should have found the subscription and set has_subscription
        assert (
            result["user"]["has_subscription"] is True
        ), f"User should have subscription. Found subscription: {found_after is not None}"
        assert (
            result["user"]["subscription"] is not None
        ), "Subscription info should be present"
        assert result["user"]["subscription"]["product_id"] == "standard_monthly"
        assert result["user"]["subscription"]["is_monthly"] is True

    @pytest.mark.asyncio
    async def test_handle_database_error(self, handler):
        """Test handling database error during sync."""
        fake_uow = FakeUnitOfWork()
        # Make the UoW raise an error
        fake_uow.users.find_by_firebase_uid_including_deleted = AsyncMock(
            side_effect=Exception("Database error")
        )
        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_error", email="error@example.com", provider="phone"
        )

        with pytest.raises(Exception, match="Database error"):
            await handler.handle(command)

        assert fake_uow.rolled_back is True

    def test_generate_username_from_email(self, handler):
        """Test username generation from email."""
        username = handler._generate_username("john.doe@example.com", None)
        assert username == "johndoe"
        assert len(username) <= 20

    def test_generate_username_from_display_name(self, handler):
        """Test username generation from display name."""
        username = handler._generate_username("any@example.com", "John Doe")
        assert username == "johndoe"

    def test_generate_username_short(self, handler):
        """Test username generation for short names."""
        username = handler._generate_username("ab@example.com", None)
        assert username == "userab"
        assert len(username) >= 3

    def test_generate_username_long(self, handler):
        """Test username generation for very long names."""
        long_email = "verylongemailaddressfortesting@example.com"
        username = handler._generate_username(long_email, None)
        assert len(username) <= 20

    def test_generate_username_special_chars(self, handler):
        """Test username generation removes special characters."""
        username = handler._generate_username("john.doe+test@example.com", None)
        assert "." not in username
        assert "+" not in username

    def test_extract_names_from_full_name(self, handler):
        """Test extracting first and last names from display name."""
        first, last = handler._extract_names("John Doe", None, None)
        assert first == "John"
        assert last == "Doe"

    def test_extract_names_from_single_name(self, handler):
        """Test extracting names when only one name provided."""
        first, last = handler._extract_names("John", None, None)
        assert first == "John"
        assert last is None

    def test_extract_names_with_multiple_parts(self, handler):
        """Test extracting names from multi-part name."""
        first, last = handler._extract_names("John Paul Doe", None, None)
        assert first == "John"
        assert last == "Paul Doe"

    def test_extract_names_from_provided_names(self, handler):
        """Test using provided first and last names."""
        first, last = handler._extract_names(None, "Jane", "Smith")
        assert first == "Jane"
        assert last == "Smith"

    def test_extract_names_prefer_provided_over_display(self, handler):
        """Test that provided names take precedence over display name."""
        first, last = handler._extract_names("Wrong Name", "Correct", "Name")
        assert first == "Correct"
        assert last == "Name"

    @pytest.mark.asyncio
    async def test_handle_without_db_session(self):
        """Test handler creates UoW when none provided."""
        handler = SyncUserCommandHandler(uow=None)
        command = SyncUserCommand(
            firebase_uid="test", email="test@example.com", provider="google"
        )

        # Handler should create UnitOfWork internally, but it will fail without real DB
        # This test verifies the handler doesn't crash when uow is None
        # In real usage, UnitOfWork() will be created
        with patch(
            "src.app.handlers.command_handlers.sync_user_command_handler.AsyncUnitOfWork"
        ) as mock_uow_class:
            mock_uow = FakeUnitOfWork()
            mock_uow_class.return_value = mock_uow
            result = await handler.handle(command)
            assert result is not None


class TestSyncUserReRegistration:
    """Test suite for re-registration after account deletion."""

    @pytest.fixture
    def handler(self):
        """Create a SyncUserCommandHandler instance."""
        return SyncUserCommandHandler()

    @pytest.mark.asyncio
    async def test_sync_creates_new_user_after_deletion(self, handler):
        """Re-authentication after deletion creates fresh account with new ID."""
        from uuid import uuid4

        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        fake_uow = FakeUnitOfWork()

        # Create and soft-delete a user (simulate account deletion)
        old_user_id = uuid4()
        deleted_user = UserDomainModel(
            id=old_user_id,
            firebase_uid="firebase_reregister",
            email="deleted@example.com",
            username="deleteduser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
            is_active=False,  # Deleted user
            onboarding_completed=True,  # Had completed onboarding before
        )
        await fake_uow.users.save(deleted_user)

        handler.uow = fake_uow

        # Re-authenticate with same Firebase UID
        command = SyncUserCommand(
            firebase_uid="firebase_reregister",
            email="deleted@example.com",
            phone_number="+1234567890",
            display_name="Returning User",
            photo_url="https://example.com/photo.jpg",
            provider="google",
        )

        result = await handler.handle(command)

        # Should create a new user
        assert result["created"] is True
        assert result["updated"] is False
        assert result["message"] == "User created successfully"

        # New user should have a different ID
        new_user_id = result["user"]["id"]
        assert str(new_user_id) != str(
            old_user_id
        ), "New user should have different ID from deleted user"

        # New user should have onboarding_completed=False (fresh start)
        assert result["user"]["onboarding_completed"] is False

        # Firebase UID should match
        assert result["user"]["firebase_uid"] == "firebase_reregister"

    @pytest.mark.asyncio
    async def test_deleted_user_record_preserved(self, handler):
        """Deleted user record is not modified on re-registration."""
        from uuid import uuid4

        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        fake_uow = FakeUnitOfWork()

        # Create and soft-delete a user
        old_user_id = uuid4()
        deleted_user = UserDomainModel(
            id=old_user_id,
            firebase_uid="firebase_preserve",
            email="old_anonymized@deleted.local",  # Anonymized email
            username="deleted_user_123",
            password_hash="",
            provider=AuthProvider.GOOGLE,
            is_active=False,
            onboarding_completed=True,
        )
        await fake_uow.users.save(deleted_user)

        handler.uow = fake_uow

        # Re-authenticate with same Firebase UID
        command = SyncUserCommand(
            firebase_uid="firebase_preserve",
            email="returning@example.com",
            display_name="Returning User",
            provider="google",
        )

        result = await handler.handle(command)

        # New user created
        assert result["created"] is True
        new_user_id = result["user"]["id"]
        assert str(new_user_id) != str(old_user_id)

        # Verify the old deleted user record is preserved unchanged
        old_user = fake_uow.users.users.get(old_user_id)
        assert old_user is not None, "Deleted user record should still exist"
        assert old_user.is_active is False, "Deleted user should remain inactive"
        assert (
            old_user.email == "old_anonymized@deleted.local"
        ), "Deleted user email should be preserved"
        assert (
            old_user.onboarding_completed is True
        ), "Deleted user onboarding should be preserved"

    @pytest.mark.asyncio
    async def test_new_user_triggers_onboarding(self, handler):
        """New user created after re-registration has fresh onboarding state."""
        from uuid import uuid4

        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        fake_uow = FakeUnitOfWork()

        # Create a deleted user who had completed onboarding
        old_user = UserDomainModel(
            id=uuid4(),
            firebase_uid="firebase_onboard",
            email="old@example.com",
            username="olduser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
            is_active=False,
            onboarding_completed=True,  # Had completed before
        )
        await fake_uow.users.save(old_user)

        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_onboard",
            email="new@example.com",
            provider="google",
        )

        result = await handler.handle(command)

        # New user should need to complete onboarding again
        assert result["created"] is True
        assert result["user"]["onboarding_completed"] is False

    @pytest.mark.asyncio
    async def test_reregistration_creates_notification_preferences(self, handler):
        """Re-registered user gets new default notification preferences."""
        from uuid import uuid4

        from src.domain.model.auth.auth_provider import AuthProvider
        from src.domain.model.user import UserDomainModel

        fake_uow = FakeUnitOfWork()

        # Create a deleted user
        old_user = UserDomainModel(
            id=uuid4(),
            firebase_uid="firebase_notif",
            email="old@example.com",
            username="olduser",
            password_hash="",
            provider=AuthProvider.GOOGLE,
            is_active=False,
        )
        await fake_uow.users.save(old_user)

        handler.uow = fake_uow

        command = SyncUserCommand(
            firebase_uid="firebase_notif",
            email="new@example.com",
            provider="google",
        )

        result = await handler.handle(command)

        # New user created
        assert result["created"] is True
        new_user_id = str(result["user"]["id"])

        # Check notification preferences were created for new user
        prefs = await fake_uow.notifications.find_notification_preferences_by_user(
            new_user_id
        )
        assert (
            prefs is not None
        ), "Notification preferences should be created for new user"

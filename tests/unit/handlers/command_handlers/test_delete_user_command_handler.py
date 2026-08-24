"""
Unit tests for DeleteUserCommandHandler.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tests.conftest import TestUserRepository

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.user import DeleteUserCommand
from src.app.handlers.command_handlers.delete_user_command_handler import (
    DeleteUserCommandHandler,
)
from src.infra.database.base import Base
from src.infra.database.models.user import User


class AsyncSqliteBulkSession:
    """Async test double for the bulk SQL operations used by delete-user."""

    def __init__(self, session):
        self._session = session

    async def execute(self, *args, **kwargs):
        return self._session.execute(*args, **kwargs)

    async def flush(self, *args, **kwargs):
        return self._session.flush(*args, **kwargs)


class AsyncSqliteUserRepository:
    """Async test double for user repository calls used by delete-user."""

    def __init__(self, session):
        self._repo = TestUserRepository(session)

    async def find_by_firebase_uid(self, *args, **kwargs):
        return self._repo.find_by_firebase_uid(*args, **kwargs)

    async def find_by_id(self, *args, **kwargs):
        return self._repo.find_by_id(*args, **kwargs)

    async def save(self, *args, **kwargs):
        return self._repo.save(*args, **kwargs)

    async def delete(self, *args, **kwargs):
        return self._repo.delete(*args, **kwargs)


class DummyUnitOfWork:
    """
    Lightweight UnitOfWork for tests that:
    - Uses the provided session (e.g., in-memory SQLite)
    - Exposes explicit async SQLite-backed test doubles
    - Commits/rolls back but NEVER closes the session
    """

    def __init__(self, session):
        self.session = AsyncSqliteBulkSession(session)
        self._raw_session = session
        self.users = AsyncSqliteUserRepository(session)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self._raw_session.rollback()
        else:
            try:
                self._raw_session.commit()
            except Exception:
                raise

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            await self.rollback()
        else:
            await self.commit()

    async def commit(self):
        self._raw_session.commit()

    async def rollback(self):
        self._raw_session.rollback()


@pytest.fixture(scope="function")
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def mock_publisher():
    """Create a mock integration event publisher."""
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def delete_handler(mock_publisher):
    """Create a DeleteUserCommandHandler instance with publisher."""
    handler = DeleteUserCommandHandler(event_publisher=mock_publisher)
    return handler


@pytest.fixture
def active_user(db_session):
    """Create an active user for testing."""
    user = User(
        email="activeuser@example.com",
        username="activeuser",
        password_hash="hashed_password",
        firebase_uid="firebase_active_123",
        first_name="Active",
        last_name="User",
        phone_number="+1234567890",
        display_name="Active User",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def inactive_user(db_session):
    """Create an inactive user for testing."""
    user = User(
        email="deleted_123@deleted.local",
        username="deleted_user_123",
        password_hash="DELETED",
        firebase_uid="firebase_deleted_123",
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


class TestDeleteUserCommandHandler:
    """Test suite for DeleteUserCommandHandler."""

    @pytest.mark.asyncio
    async def test_delete_active_user_successfully(
        self, delete_handler, mock_publisher, active_user, db_session
    ):
        """Test successfully deleting an active user and publishing UserDeletedEvent."""
        command = DeleteUserCommand(firebase_uid=active_user.firebase_uid)

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            result = await delete_handler.handle(command)

            assert result["firebase_uid"] == active_user.firebase_uid
            assert result["deleted"] is True
            assert result["firebase_cleanup_queued"] is True
            assert (
                result["message"]
                == "Account successfully deleted; Firebase cleanup queued"
            )

            # Verify user is soft deleted in DB
            deleted_user = (
                db_session.query(User).filter(User.id == active_user.id).first()
            )
            assert deleted_user.is_active is False

            # Verify event was published
            mock_publisher.publish.assert_awaited_once()
            payload = mock_publisher.publish.call_args[0][0]
            assert payload["event_type"] == "user.deleted.v1"
            assert payload["aggregate_id"] == str(active_user.id)
            assert payload["data"]["firebase_uid"] == active_user.firebase_uid
            assert payload["data"]["user_id"] == str(active_user.id)

    @pytest.mark.asyncio
    async def test_anonymize_user_data_on_deletion(
        self, delete_handler, active_user, db_session
    ):
        """Test that user data is anonymized during deletion."""
        command = DeleteUserCommand(firebase_uid=active_user.firebase_uid)
        user_id = active_user.id

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            await delete_handler.handle(command)

            deleted_user = db_session.query(User).filter(User.id == user_id).first()
            assert deleted_user.email == f"deleted_{user_id}@deleted.local"
            assert deleted_user.username == f"deleted_user_{user_id}"
            assert deleted_user.first_name is None
            assert deleted_user.last_name is None
            assert deleted_user.phone_number is None
            assert deleted_user.display_name is None
            assert deleted_user.photo_url is None
            assert deleted_user.password_hash == "DELETED"

    @pytest.mark.asyncio
    async def test_delete_inactive_user_raises_not_found(
        self, delete_handler, inactive_user, db_session
    ):
        """Test that deleting an inactive user raises ResourceNotFoundException."""
        command = DeleteUserCommand(firebase_uid=inactive_user.firebase_uid)

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            with pytest.raises(ResourceNotFoundException):
                await delete_handler.handle(command)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user_raises_not_found(
        self, delete_handler, db_session
    ):
        """Test that deleting a non-existent user raises ResourceNotFoundException."""
        command = DeleteUserCommand(firebase_uid="nonexistent_firebase_uid")

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            with pytest.raises(ResourceNotFoundException):
                await delete_handler.handle(command)

    @pytest.mark.asyncio
    async def test_publisher_failure_does_not_rollback_db(
        self, active_user, db_session
    ):
        """Test that event publisher failure does not rollback committed database changes."""
        failing_publisher = MagicMock()
        failing_publisher.publish = AsyncMock(side_effect=Exception("Queue down"))
        handler = DeleteUserCommandHandler(event_publisher=failing_publisher)

        command = DeleteUserCommand(firebase_uid=active_user.firebase_uid)
        user_id = active_user.id

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            result = await handler.handle(command)

            assert result["deleted"] is True
            deleted_user = db_session.query(User).filter(User.id == user_id).first()
            assert deleted_user.is_active is False
            assert deleted_user.email == f"deleted_{user_id}@deleted.local"

    @pytest.mark.asyncio
    async def test_missing_db_session_raises_runtime_error(self, delete_handler):
        """Test that missing database session raises RuntimeError."""
        command = DeleteUserCommand(firebase_uid="some_uid")

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with pytest.raises(RuntimeError, match="DB unavailable"):
                await delete_handler.handle(command)

    @pytest.mark.asyncio
    async def test_delete_user_preserves_user_id_in_anonymized_email(
        self, delete_handler, active_user, db_session
    ):
        """Test that anonymized email preserves user ID for audit trail."""
        command = DeleteUserCommand(firebase_uid=active_user.firebase_uid)
        user_id = active_user.id

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            await delete_handler.handle(command)

            deleted_user = db_session.query(User).filter(User.id == user_id).first()
            assert str(user_id) in deleted_user.email
            assert str(user_id) in deleted_user.username

    @pytest.mark.asyncio
    async def test_delete_handles_exception_and_logs(
        self, delete_handler, active_user, db_session
    ):
        """Test that exceptions during DB transaction are properly handled and rolled back."""
        command = DeleteUserCommand(firebase_uid=active_user.firebase_uid)

        class FailingUnitOfWork(DummyUnitOfWork):
            async def commit(self):
                raise Exception("DB Error")

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=FailingUnitOfWork(db_session)),
        ):
            with pytest.raises(Exception, match="Failed to delete user account"):
                await delete_handler.handle(command)


class TestDeleteUserCommandHandlerIntegration:
    """Integration tests for DeleteUserCommandHandler with actual database operations."""

    @pytest.mark.asyncio
    async def test_complete_deletion_flow(self, db_session):
        """Test complete user deletion flow with handler."""
        user = User(
            email="integration_test@example.com",
            username="integration_test_user",
            password_hash="hashed_password",
            firebase_uid="firebase_integration_123",
            first_name="Integration",
            last_name="Test",
            phone_number="+1987654321",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        publisher = MagicMock(publish=AsyncMock())
        handler = DeleteUserCommandHandler(event_publisher=publisher)
        command = DeleteUserCommand(firebase_uid=user.firebase_uid)

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            result = await handler.handle(command)

            assert result["deleted"] is True

            db_user = db_session.query(User).filter(User.id == user.id).first()
            assert db_user.is_active is False
            assert "deleted_" in db_user.email
            assert db_user.password_hash == "DELETED"
            publisher.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_users_deletion_isolation(self, db_session):
        """Test that deleting one user doesn't affect other users."""
        user1 = User(
            email="user1@example.com",
            username="user1",
            password_hash="pwd1",
            firebase_uid="firebase_1",
            is_active=True,
        )
        user2 = User(
            email="user2@example.com",
            username="user2",
            password_hash="pwd2",
            firebase_uid="firebase_2",
            is_active=True,
        )
        db_session.add_all([user1, user2])
        db_session.commit()

        handler = DeleteUserCommandHandler()

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            await handler.handle(DeleteUserCommand(firebase_uid=user1.firebase_uid))

            db_user1 = db_session.query(User).filter(User.id == user1.id).first()
            db_user2 = db_session.query(User).filter(User.id == user2.id).first()

            assert db_user1.is_active is False
            assert db_user2.is_active is True
            assert db_user2.email == "user2@example.com"

    @pytest.mark.asyncio
    async def test_soft_delete_preserves_historical_data_for_audit(self, db_session):
        """Test that soft delete preserves data for audit trail."""
        user = User(
            email="audit_test@example.com",
            username="audit_user",
            password_hash="original_hash",
            firebase_uid="firebase_audit",
            first_name="Audit",
            last_name="Test",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        original_created = user.created_at
        original_id = user.id

        handler = DeleteUserCommandHandler()

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            await handler.handle(DeleteUserCommand(firebase_uid=user.firebase_uid))

            db_user = db_session.query(User).filter(User.id == original_id).first()
            assert db_user.id == original_id
            assert db_user.created_at == original_created
            assert db_user.is_active is False

    @pytest.mark.asyncio
    async def test_delete_sets_deleted_at_timestamp(self, db_session):
        """Test that deletion sets deleted_at timestamp for audit trail."""
        user = User(
            email="timestamp_test@example.com",
            username="timestamp_user",
            password_hash="hashed_password",
            firebase_uid="firebase_timestamp_123",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        assert user.deleted_at is None

        handler = DeleteUserCommandHandler()

        with patch(
            "src.app.handlers.command_handlers.delete_user_command_handler.AsyncUnitOfWork",
            MagicMock(return_value=DummyUnitOfWork(db_session)),
        ):
            await handler.handle(DeleteUserCommand(firebase_uid=user.firebase_uid))

            db_user = db_session.query(User).filter(User.id == user.id).first()
            assert db_user.deleted_at is not None
            assert db_user.is_active is False

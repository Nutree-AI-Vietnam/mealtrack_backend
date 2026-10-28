"""
Integration tests for account deletion on real PostgreSQL.

The handler opens its own AsyncUnitOfWork, so each test commits its users
through a per-test async engine that belongs to the test's event loop.
Firebase cleanup is queued as an integration event, never run in the request.
"""

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.user import DeleteUserCommand
from src.app.handlers.command_handlers.delete_user_command_handler import (
    DeleteUserCommandHandler,
)
from src.infra.database.models.user import User
from src.infra.event_bus import PyMediatorEventBus
from tests.fixtures.mock_image_store import MockImageStore

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db(test_engine, monkeypatch) -> AsyncIterator[async_sessionmaker]:
    """Async sessions on the test database (schema built by ``test_engine``)."""
    url = test_engine.url.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("src.infra.database.uow_async.AsyncSessionLocal", factory)
    await _clear_users(factory)
    try:
        yield factory
    finally:
        await _clear_users(factory)
        await engine.dispose()


async def _clear_users(factory) -> None:
    async with factory() as session:
        await session.execute(text("TRUNCATE TABLE users RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest.fixture
def publisher() -> MagicMock:
    return MagicMock(publish=AsyncMock())


@pytest.fixture
def image_store() -> MockImageStore:
    return MockImageStore()


@pytest.fixture
def bus(publisher, image_store) -> PyMediatorEventBus:
    event_bus = PyMediatorEventBus()
    event_bus.register_handler(
        DeleteUserCommand,
        DeleteUserCommandHandler(event_publisher=publisher, image_store=image_store),
    )
    return event_bus


async def _add_user(db, name: str, **fields) -> User:
    user = User(
        email=f"{name}@example.com",
        username=name,
        password_hash="hashed_password",
        firebase_uid=f"firebase_{name}",
        is_active=True,
        **fields,
    )
    async with db() as session:
        session.add(user)
        await session.commit()
    return user


async def _row(db, user_id: str) -> User:
    async with db() as session:
        return (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()


class TestDeleteAccountIntegration:
    """Integration tests for complete delete account workflow."""

    @pytest.mark.asyncio
    async def test_delete_account_complete_flow(self, db, bus):
        user = await _add_user(db, "complete", first_name="Integration")

        result = await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        assert result["deleted"] is True
        assert result["firebase_uid"] == "firebase_complete"
        deleted = await _row(db, user.id)
        assert deleted.is_active is False
        assert deleted.deleted_at is not None
        assert "deleted_" in deleted.email

    @pytest.mark.asyncio
    async def test_delete_user_data_anonymization(self, db, bus):
        user = await _add_user(
            db,
            "anonymize",
            first_name="Original",
            last_name="Name",
            phone_number="+1987654321",
            display_name="Test User",
            photo_url="https://provider.example/p.jpg",
        )

        await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        deleted = await _row(db, user.id)
        assert deleted.email == f"deleted_{user.id}@deleted.local"
        assert deleted.username == f"deleted_user_{user.id}"
        assert deleted.firebase_uid == f"deleted:{user.id}"
        assert deleted.first_name is None
        assert deleted.last_name is None
        assert deleted.phone_number is None
        assert deleted.display_name is None
        assert deleted.photo_url is None
        assert deleted.password_hash == "DELETED"

    @pytest.mark.asyncio
    async def test_multiple_users_deletion_isolation(self, db, bus):
        first = await _add_user(db, "isolation_one")
        second = await _add_user(db, "isolation_two")

        await bus.send(DeleteUserCommand(firebase_uid=first.firebase_uid))

        assert (await _row(db, first.id)).is_active is False
        untouched = await _row(db, second.id)
        assert untouched.is_active is True
        assert untouched.email == "isolation_two@example.com"

    @pytest.mark.asyncio
    async def test_delete_preserves_audit_trail(self, db, bus):
        user = await _add_user(db, "audit")
        created_at = (await _row(db, user.id)).created_at

        await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        deleted = await _row(db, user.id)
        assert deleted.id == user.id
        assert deleted.created_at == created_at
        assert deleted.is_active is False

    @pytest.mark.asyncio
    async def test_deletion_queues_firebase_cleanup(self, db, bus, publisher):
        user = await _add_user(db, "firebase_queue")

        result = await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        assert result["firebase_cleanup_queued"] is True
        assert result["firebase_deleted"] is False
        publisher.publish.assert_awaited_once()
        payload = publisher.publish.call_args[0][0]
        assert payload["data"]["firebase_uid"] == "firebase_firebase_queue"
        assert (await _row(db, user.id)).is_active is False

    @pytest.mark.asyncio
    async def test_deleted_user_filtered_from_queries(self, db, bus):
        user = await _add_user(db, "filter")

        await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        async with db() as session:
            active = await session.execute(
                select(User).where(
                    User.firebase_uid == "firebase_filter", User.is_active.is_(True)
                )
            )
            assert active.scalar_one_or_none() is None
        kept = await _row(db, user.id)
        assert kept.is_active is False
        assert kept.firebase_uid == f"deleted:{user.id}"

    @pytest.mark.asyncio
    async def test_second_deletion_of_the_same_account_is_not_found(self, db, bus):
        user = await _add_user(db, "twice")
        await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        with pytest.raises(ResourceNotFoundException):
            await bus.send(DeleteUserCommand(firebase_uid="firebase_twice"))

        assert (await _row(db, user.id)).is_active is False


class TestDeleteAccountAvatar:
    """The avatar is reset in the deletion transaction; its photo goes after commit."""

    @pytest.mark.asyncio
    async def test_deletion_resets_the_avatar_and_deletes_its_photo(
        self, db, bus, image_store
    ):
        image_store.storage["avatar-photo"] = b"jpeg"
        user = await _add_user(
            db,
            "avatar_photo",
            avatar_kind="photo",
            avatar_image_id="avatar-photo",
            avatar_image_url="https://img.example/avatar-photo",
        )

        result = await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        assert result["deleted"] is True
        deleted = await _row(db, user.id)
        assert deleted.avatar_kind == "auto"
        assert deleted.avatar_image_id is None
        assert deleted.avatar_image_url is None
        assert deleted.avatar_character is None
        assert deleted.avatar_color is None
        assert "avatar-photo" not in image_store.storage

    @pytest.mark.asyncio
    async def test_character_avatar_is_reset_without_touching_storage(
        self, db, bus, image_store
    ):
        image_store.delete_async = AsyncMock(return_value=True)
        user = await _add_user(
            db, "avatar_character", avatar_kind="character", avatar_character="lemon"
        )

        await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        deleted = await _row(db, user.id)
        assert deleted.avatar_kind == "auto"
        assert deleted.avatar_character is None
        image_store.delete_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failing_photo_delete_is_retried_then_logged(
        self, db, bus, image_store, caplog
    ):
        image_store.delete_async = AsyncMock(return_value=False)
        user = await _add_user(
            db,
            "avatar_stuck",
            avatar_kind="photo",
            avatar_image_id="stuck-photo",
            avatar_image_url="https://img.example/stuck-photo",
        )

        with caplog.at_level(
            logging.ERROR, logger="src.app.services.user_avatar_writes"
        ):
            result = await bus.send(DeleteUserCommand(firebase_uid=user.firebase_uid))

        assert result["deleted"] is True
        assert image_store.delete_async.await_count == 3
        assert any("stuck-photo" in r.getMessage() for r in caplog.records)
        deleted = await _row(db, user.id)
        assert deleted.is_active is False
        assert deleted.avatar_kind == "auto"

"""Regression for PYTHON-JT.

Soft-deleted rows used to keep firebase_uid while find_by_firebase_uid only
returns active users. POST /v1/users/sync then inserted a second row and
Postgres rejected it on ix_users_firebase_uid. Retries keep failing because
the conflicting row stays inactive.

This test uses the real UNIQUE column on users.firebase_uid. Catching
IntegrityError without releasing that key does not make it pass.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tests.conftest import AsyncTestUserRepository
from tests.fixtures.fakes.fake_notification_repository import FakeNotificationRepository
from tests.fixtures.fakes.fake_subscription_repository import FakeSubscriptionRepository
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.user.sync_user_command import SyncUserCommand
from src.app.handlers.command_handlers.sync_user_command_handler import (
    SyncUserCommandHandler,
)
from src.domain.exceptions.firebase_identity_exceptions import (
    FirebaseIdentityConflictError,
)
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.user import UserDomainModel
from src.infra.database.base import Base
from src.infra.database.models.user import User


class _SqliteSyncUnitOfWork:
    """SQLite-backed users plus in-memory notification and subscription repos."""

    def __init__(self, session):
        self.session = session
        self.users = AsyncTestUserRepository(session)
        self.notifications = FakeNotificationRepository()
        self.subscriptions = FakeSubscriptionRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            self.session.rollback()
        return False

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
def sqlite_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _inactive_row_holding_live_uid(session, firebase_uid: str) -> str:
    """Insert the production shape: anonymized, inactive, uid still unique."""
    user_id = str(uuid4())
    session.add(
        User(
            id=user_id,
            email=f"deleted_{user_id}@deleted.local",
            username=f"deleted_user_{user_id}",
            password_hash="DELETED",
            firebase_uid=firebase_uid,
            provider=AuthProvider.GOOGLE,
            is_active=False,
            onboarding_completed=True,
            deleted_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    session.commit()
    return user_id


@pytest.mark.asyncio
async def test_sync_creates_new_account_when_inactive_row_holds_firebase_uid(
    sqlite_session,
):
    """Re-sync must free ix_users_firebase_uid on the inactive row, then insert."""
    firebase_uid = "firebase-returning-user"
    old_user_id = _inactive_row_holding_live_uid(sqlite_session, firebase_uid)
    handler = SyncUserCommandHandler(uow=_SqliteSyncUnitOfWork(sqlite_session))

    result = await handler.handle(
        SyncUserCommand(
            firebase_uid=firebase_uid,
            email="returning@example.com",
            display_name="Returning User",
            provider="google",
        )
    )

    assert result["created"] is True
    assert result["user"]["firebase_uid"] == firebase_uid
    assert result["user"]["is_active"] is True
    assert result["user"]["onboarding_completed"] is False
    assert str(result["user"]["id"]) != old_user_id

    rows = sqlite_session.query(User).all()
    live_rows = [row for row in rows if row.firebase_uid == firebase_uid]
    assert len(live_rows) == 1
    assert live_rows[0].is_active is True
    assert str(live_rows[0].id) != old_user_id

    audit = sqlite_session.query(User).filter(User.id == old_user_id).one()
    assert audit.is_active is False
    assert audit.onboarding_completed is True
    assert audit.email == f"deleted_{old_user_id}@deleted.local"
    assert audit.firebase_uid == f"deleted:{old_user_id}"
    assert audit.firebase_uid != firebase_uid


@pytest.mark.asyncio
async def test_reregistration_uses_one_identity_lookup_then_releases_uid():
    """PYTHON-JY's single lookup stays one SELECT; the inactive uid is still released.

    A second find_deleted_by_firebase_uid would put the N+1 back. Tombstoning
    is an UPDATE of the row that lookup already returned.
    """
    old_user_id = uuid4()
    fake_uow = FakeUnitOfWork()
    await fake_uow.users.save(
        UserDomainModel(
            id=old_user_id,
            firebase_uid="firebase-one-lookup",
            email="old@deleted.local",
            username="deleted_user_one",
            password_hash="DELETED",
            provider=AuthProvider.GOOGLE,
            is_active=False,
            onboarding_completed=True,
        )
    )
    calls: list[str] = []
    users = fake_uow.users
    original_any = users.find_by_firebase_uid_including_deleted
    original_active = users.find_by_firebase_uid
    # #739 removed this from the port. Installing the spy still fails the
    # test if sync starts calling it again.
    original_deleted = getattr(users, "find_deleted_by_firebase_uid", None)

    async def find_any(firebase_uid: str):
        calls.append("including_deleted")
        return await original_any(firebase_uid)

    async def find_active(firebase_uid: str):
        calls.append("active")
        return await original_active(firebase_uid)

    async def find_deleted(firebase_uid: str):
        calls.append("deleted")
        if original_deleted is None:
            return None
        return await original_deleted(firebase_uid)

    users.find_by_firebase_uid_including_deleted = find_any
    users.find_by_firebase_uid = find_active
    users.find_deleted_by_firebase_uid = find_deleted
    handler = SyncUserCommandHandler(uow=fake_uow)

    result = await handler.handle(
        SyncUserCommand(
            firebase_uid="firebase-one-lookup",
            email="returning-one@example.com",
            provider="google",
        )
    )

    assert calls == ["including_deleted"]
    assert result["created"] is True
    assert str(result["user"]["id"]) != str(old_user_id)
    audit = fake_uow.users.users[old_user_id]
    assert audit.is_active is False
    assert audit.firebase_uid == f"deleted:{old_user_id}"


@pytest.mark.asyncio
async def test_sync_adopts_the_active_row_that_wins_the_insert_race():
    """A concurrent insert that already committed must be updated, not rejected."""
    winner_id = uuid4()
    fake_uow = FakeUnitOfWork()
    saves = {"count": 0}

    async def save(user: UserDomainModel) -> UserDomainModel:
        saves["count"] += 1
        if saves["count"] == 1:
            winner = UserDomainModel(
                id=winner_id,
                firebase_uid=user.firebase_uid,
                email="winner@example.com",
                username="winner",
                password_hash="",
                provider=AuthProvider.GOOGLE,
                is_active=True,
            )
            fake_uow.users.users[winner_id] = winner
            raise FirebaseIdentityConflictError("ix_users_firebase_uid")
        fake_uow.users.users[user.id] = user
        return user

    fake_uow.users.save = save
    handler = SyncUserCommandHandler(uow=fake_uow)

    result = await handler.handle(
        SyncUserCommand(
            firebase_uid="firebase-race",
            email="winner@example.com",
            display_name="Winner",
            provider="google",
        )
    )

    assert result["created"] is False
    assert result["updated"] is True
    assert str(result["user"]["id"]) == str(winner_id)
    assert result["user"]["firebase_uid"] == "firebase-race"
    assert result["user"]["display_name"] == "Winner"
    assert set(fake_uow.users.users) == {winner_id}
    assert fake_uow.committed is True
    assert fake_uow.rolled_back is False
    assert saves["count"] == 2


@pytest.mark.asyncio
async def test_sync_still_rejects_a_firebase_uid_conflict_with_no_active_row():
    """Email or unknown unique conflicts stay conflicts when no active row won."""
    fake_uow = FakeUnitOfWork()
    fake_uow.users.save = AsyncMock(
        side_effect=FirebaseIdentityConflictError("users_email_key")
    )
    handler = SyncUserCommandHandler(uow=fake_uow)

    with pytest.raises(FirebaseIdentityConflictError):
        await handler.handle(
            SyncUserCommand(
                firebase_uid="firebase-email-conflict",
                email="taken@example.com",
                provider="google",
            )
        )

    assert fake_uow.rolled_back is True

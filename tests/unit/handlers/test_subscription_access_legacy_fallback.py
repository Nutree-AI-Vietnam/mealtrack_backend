"""Sign-in and profile reads never fail on an unreconciled subscription row.

With SUBSCRIPTION_ACCESS_ENABLED on, the access policy raises
SubscriptionAccessUnavailable for a user whose only rows were never verified
by a provider snapshot. POST /v1/users/sync and GET /v1/users/firebase/{uid}
must fall back to the legacy decision instead of answering 500.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from tests.fixtures.fakes.fake_uow import FakeUnitOfWork

from src.app.commands.user.sync_user_command import SyncUserCommand
from src.app.handlers.command_handlers.sync_user_command_handler import (
    SyncUserCommandHandler,
)
from src.app.handlers.query_handlers.get_user_by_firebase_uid_query_handler import (
    GetUserByFirebaseUidQueryHandler,
)
from src.app.queries.user.get_user_by_firebase_uid_query import (
    GetUserByFirebaseUidQuery,
)
from src.domain.model.auth.auth_provider import AuthProvider
from src.domain.model.subscription import Subscription
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.model.user import UserDomainModel


@pytest.fixture(autouse=True)
def _database_access_on(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")
    monkeypatch.setenv("ENVIRONMENT", "test")


async def _user_with_rows(uow: FakeUnitOfWork, *rows: Subscription) -> UserDomainModel:
    user = UserDomainModel(
        id=uuid4(),
        firebase_uid="firebase-unreconciled",
        email="unreconciled@example.com",
        username="unreconciled",
        password_hash="",
        provider=AuthProvider.GOOGLE,
    )
    await uow.users.save(user)
    for row in rows:
        row.user_id = str(user.id)
        await uow.subscriptions.save(row)
    return user


def _unreconciled(**overrides) -> Subscription:
    values = {
        "user_id": "",
        "product_id": "standard_monthly",
        "status": "active",
        # Naive like the fake repository's datetime.now() comparison.
        "expires_at": datetime.now() + timedelta(days=20),
        "platform": "ios",
        "provider_snapshot_at": None,
    }
    values.update(overrides)
    return Subscription(**values)


def _assert_policy_raises(rows: list[Subscription]) -> None:
    with pytest.raises(SubscriptionAccessUnavailable):
        evaluate_subscription_access(rows)


@pytest.mark.asyncio
async def test_users_sync_falls_back_to_legacy_grant_for_unreconciled_row(caplog):
    uow = FakeUnitOfWork()
    row = _unreconciled()
    await _user_with_rows(uow, row)
    _assert_policy_raises([row])

    with caplog.at_level(logging.WARNING):
        result = await SyncUserCommandHandler(uow=uow).handle(
            SyncUserCommand(
                firebase_uid="firebase-unreconciled",
                email="unreconciled@example.com",
                provider="google",
            )
        )

    assert result["user"]["has_subscription"] is True
    assert result["user"]["subscription"]["product_id"] == "standard_monthly"
    assert uow.committed is True
    assert "using the legacy decision: source=users_sync" in caplog.text


@pytest.mark.asyncio
async def test_users_sync_long_expired_unreconciled_row_is_no_subscription(caplog):
    uow = FakeUnitOfWork()
    # A 'cancelled' row that expired a year ago was never reconciled: the
    # policy cannot decide, so it raises; sign-in must still succeed.
    row = _unreconciled(
        status="cancelled",
        expires_at=datetime(2025, 1, 1, tzinfo=UTC).replace(tzinfo=None),
    )
    await _user_with_rows(uow, row)
    _assert_policy_raises([row])

    with caplog.at_level(logging.WARNING):
        result = await SyncUserCommandHandler(uow=uow).handle(
            SyncUserCommand(
                firebase_uid="firebase-unreconciled",
                email="unreconciled@example.com",
                provider="google",
            )
        )

    assert result["user"]["has_subscription"] is False
    assert result["user"]["subscription"] is None
    assert "source=users_sync" in caplog.text


@pytest.mark.asyncio
async def test_users_sync_still_uses_policy_when_it_can_decide():
    uow = FakeUnitOfWork()
    verified = _unreconciled(
        expires_at=datetime.now(UTC) + timedelta(days=3),
        provider_snapshot_at=datetime.now(UTC),
    )
    await _user_with_rows(uow, verified)

    shorter = _unreconciled(
        user_id=verified.user_id,
        product_id="standard_weekly",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        provider_snapshot_at=datetime.now(UTC),
    )
    await uow.subscriptions.save(shorter)

    result = await SyncUserCommandHandler(uow=uow).handle(
        SyncUserCommand(
            firebase_uid="firebase-unreconciled",
            email="unreconciled@example.com",
            provider="google",
        )
    )

    assert result["user"]["has_subscription"] is True
    # Describes the granting row with the latest deadline.
    assert result["user"]["subscription"]["product_id"] == "standard_monthly"


@pytest.mark.asyncio
async def test_profile_fetch_falls_back_to_legacy_decision_for_unreconciled_row(
    caplog,
):
    uow = FakeUnitOfWork()
    row = _unreconciled()
    await _user_with_rows(uow, row)
    _assert_policy_raises([row])
    handler = GetUserByFirebaseUidQueryHandler(uow_factory=lambda: uow)

    with caplog.at_level(logging.WARNING):
        result = await handler.handle(
            GetUserByFirebaseUidQuery(firebase_uid="firebase-unreconciled")
        )

    # The legacy decision for this endpoint is user.has_active_subscription().
    assert result["has_subscription"] is False
    assert result["firebase_uid"] == "firebase-unreconciled"
    assert "using the legacy decision: source=users_profile" in caplog.text


@pytest.mark.asyncio
async def test_profile_fetch_uses_policy_grant_when_rows_are_verified():
    uow = FakeUnitOfWork()
    verified = _unreconciled(
        expires_at=datetime.now(UTC) + timedelta(days=3),
        provider_snapshot_at=datetime.now(UTC),
    )
    await _user_with_rows(uow, verified)
    handler = GetUserByFirebaseUidQueryHandler(uow_factory=lambda: uow)

    result = await handler.handle(
        GetUserByFirebaseUidQuery(firebase_uid="firebase-unreconciled")
    )

    assert result["has_subscription"] is True

"""Profile lookup with database subscription access turned on."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.handlers.query_handlers import get_user_by_firebase_uid_query_handler
from src.app.handlers.query_handlers.get_user_by_firebase_uid_query_handler import (
    GetUserByFirebaseUidQueryHandler,
)
from src.app.queries.user.get_user_by_firebase_uid_query import (
    GetUserByFirebaseUidQuery,
)


def _user():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    return SimpleNamespace(
        id="user-1",
        firebase_uid="fb-1",
        email="a@example.com",
        username="a",
        first_name=None,
        last_name=None,
        phone_number=None,
        display_name=None,
        photo_url=None,
        provider="google",
        is_active=True,
        onboarding_completed=True,
        last_accessed=now,
        created_at=now,
        updated_at=now,
    )


def _unreconciled():
    # Active, but never verified against the provider.
    return SimpleNamespace(
        status="active",
        expires_at=None,
        grace_period_expires_at=None,
        period_type=None,
        provider_snapshot_at=None,
    )


class _Uow:
    def __init__(self, subscriptions):
        self.users = SimpleNamespace(
            find_by_firebase_uid=AsyncMock(return_value=_user())
        )
        self.subscriptions = SimpleNamespace(
            find_by_user_id=AsyncMock(return_value=subscriptions)
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_unreconciled_subscription_loads_profile_without_access(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_ACCESS_ENABLED", "true")
    monkeypatch.setattr(
        get_user_by_firebase_uid_query_handler,
        "AsyncUnitOfWork",
        lambda: _Uow([_unreconciled()]),
    )

    result = await GetUserByFirebaseUidQueryHandler().handle(
        GetUserByFirebaseUidQuery(firebase_uid="fb-1")
    )

    assert result["id"] == "user-1"
    assert result["has_subscription"] is False

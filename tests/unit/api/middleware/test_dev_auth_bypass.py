"""Tests for development auth bypass user ensure."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from src.api.middleware import dev_auth_bypass


class _ScalarResult:
    def __init__(self, user):
        self._user = user

    def scalars(self):
        return self

    def first(self):
        return self._user


class _Session:
    def __init__(self, execute_users, *, flush_error=None):
        self._execute_users = list(execute_users)
        self.flush_error = flush_error
        self.added = []
        self.rollback = AsyncMock()
        self.close = AsyncMock()
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        user = self._execute_users.pop(0) if self._execute_users else None
        return _ScalarResult(user)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        if self.flush_error is not None:
            raise self.flush_error


@pytest.mark.asyncio
async def test_ensure_dev_user_reuses_row_with_same_email(monkeypatch):
    existing = SimpleNamespace(
        id="existing-id",
        firebase_uid="older-uid",
        email="localalex@example.com",
        username="dev_user",
    )
    session = _Session([None, existing])
    monkeypatch.setenv("DEV_USER_FIREBASE_UID", "arkPj1IdgRYHXmE08tWgaT3q7rO2")
    monkeypatch.setenv("DEV_USER_EMAIL", "localalex@example.com")
    monkeypatch.setattr(dev_auth_bypass, "AsyncSessionLocal", lambda: session)

    user = await dev_auth_bypass._ensure_dev_user_async()

    assert user is existing
    assert session.added == []


@pytest.mark.asyncio
async def test_ensure_dev_user_recovers_from_email_unique_violation(monkeypatch):
    existing = SimpleNamespace(
        id="existing-id",
        firebase_uid="older-uid",
        email="localalex@example.com",
        username="dev_user",
    )
    session = _Session(
        [None, None, None, existing],
        flush_error=IntegrityError("INSERT", {}, Exception("users_email_key")),
    )
    monkeypatch.setenv("DEV_USER_FIREBASE_UID", "arkPj1IdgRYHXmE08tWgaT3q7rO2")
    monkeypatch.setenv("DEV_USER_EMAIL", "localalex@example.com")
    monkeypatch.setattr(dev_auth_bypass, "AsyncSessionLocal", lambda: session)

    user = await dev_auth_bypass._ensure_dev_user_async()

    assert user is existing
    session.rollback.assert_awaited()

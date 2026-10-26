"""POST /v1/notifications/tokens end to end on real PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text

from src.api.dependencies.ai_jobs import get_push_token_service
from src.api.dependencies.auth import get_current_user_id
from src.api.exception_handlers import register_exception_handlers
from src.api.routes.v1 import notifications as notifications_mod
from src.app.services.ai_jobs import AiJobsSchemaProbe, PushTokenService
from src.infra.database.models.notification.user_fcm_token import (
    UserFcmTokenORM as Token,
)
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

pytestmark = pytest.mark.integration

TOKEN = "device-token-1"

# Staging's user_fcm_tokens was built by create_all from the model removed in
# August, whose timestamps had Python-side defaults only.
_DROP_TIMESTAMP_DEFAULTS = (
    "ALTER TABLE user_fcm_tokens"
    " ALTER COLUMN created_at DROP DEFAULT,"
    " ALTER COLUMN updated_at DROP DEFAULT"
)
_RESTORE_TIMESTAMP_DEFAULTS = (
    "ALTER TABLE user_fcm_tokens"
    " ALTER COLUMN created_at SET DEFAULT now(),"
    " ALTER COLUMN updated_at SET DEFAULT now()"
)


async def _execute(factory, sql: str) -> None:
    async with factory() as session:
        await session.execute(text(sql))
        await session.commit()


@pytest_asyncio.fixture
async def staging_token_table(monkeypatch, async_session_factory):
    """Token table without column defaults; each request commits on its own."""
    monkeypatch.setattr(
        "src.infra.database.uow_async.AsyncSessionLocal", async_session_factory
    )
    await _execute(async_session_factory, _DROP_TIMESTAMP_DEFAULTS)
    try:
        yield async_session_factory
    finally:
        await _execute(async_session_factory, _RESTORE_TIMESTAMP_DEFAULTS)


async def _user(session, name: str) -> str:
    user = User(
        id=str(uuid4()),
        firebase_uid=f"uid-{name}-{uuid4()}",
        email=f"{name}-{uuid4()}@example.com",
        username=f"{name}-{uuid4()}",
        password_hash="unused",
    )
    session.add(user)
    await session.commit()
    return user.id


def _app(signed_in: Callable[[], str]) -> FastAPI:
    tokens = PushTokenService(
        uow_factory=AsyncUnitOfWork, schema_probe=AiJobsSchemaProbe(AsyncUnitOfWork)
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(notifications_mod.router)
    app.dependency_overrides[get_current_user_id] = signed_in
    app.dependency_overrides[get_push_token_service] = lambda: tokens
    return app


async def _stored(factory) -> Token:
    async with factory() as session:
        return (
            await session.execute(select(Token).where(Token.fcm_token == TOKEN))
        ).scalar_one()


@pytest.mark.asyncio
async def test_fresh_and_repeat_registrations_are_stored(
    pg_session, staging_token_table
):
    alice = await _user(pg_session, "alice")
    bob = await _user(pg_session, "bob")
    signed_in = {"user_id": alice}
    app = _app(lambda: signed_in["user_id"])
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:

        async def register() -> None:
            response = await api.post(
                "/v1/notifications/tokens",
                json={"fcm_token": TOKEN, "device_type": "ios"},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"success": True, "message": "Token registered"}

        await register()
        first = await _stored(staging_token_table)
        await register()  # the same device on its next launch
        again = await _stored(staging_token_table)
        signed_in["user_id"] = bob  # another account signs in on that phone
        await register()
        moved = await _stored(staging_token_table)

    assert first.user_id == alice and first.is_active
    assert first.created_at is not None and first.updated_at is not None
    assert again.id == first.id and again.created_at == first.created_at
    assert again.updated_at > first.updated_at
    assert again.last_registered_at > first.last_registered_at
    assert moved.id == first.id and moved.user_id == bob and moved.is_active

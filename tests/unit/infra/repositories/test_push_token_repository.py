"""SQL that ``AsyncPushTokenRepository`` sends when a device registers."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.domain.model.notification import DeviceType
from src.infra.repositories.push_token_repository import AsyncPushTokenRepository

_UPSERT = re.compile(
    r"INSERT INTO user_fcm_tokens \((?P<columns>.+?)\) VALUES \((?P<values>.+?)\)"
    r" ON CONFLICT \(fcm_token\) DO UPDATE SET (?P<updates>.+)"
)


async def _upsert_sql() -> str:
    session = MagicMock()
    session.execute = AsyncMock()
    await AsyncPushTokenRepository(session).upsert(
        user_id="user-1", token="token-1", device_type=DeviceType.IOS
    )
    [call] = session.execute.await_args_list
    return str(call.args[0].compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_registration_writes_both_timestamps_itself():
    # Some databases have no DEFAULT on these columns (see UserFcmTokenORM), so
    # leaving them out of the INSERT stores NULL and fails the NOT NULL check.
    sql = await _upsert_sql()

    match = _UPSERT.fullmatch(sql)
    assert match, sql
    inserted = dict(
        zip(
            match["columns"].split(", "),
            match["values"].split(", "),
            strict=True,
        )
    )
    assert inserted.get("created_at") == "now()", sql
    assert inserted.get("updated_at") == "now()", sql


@pytest.mark.asyncio
async def test_repeat_registration_keeps_created_at():
    sql = await _upsert_sql()

    match = _UPSERT.fullmatch(sql)
    assert match, sql
    updated = dict(
        assignment.split(" = ", 1) for assignment in match["updates"].split(", ")
    )
    assert updated.get("updated_at") == "now()", sql
    assert "created_at" not in updated, sql

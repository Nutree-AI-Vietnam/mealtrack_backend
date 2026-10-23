"""Unit tests for AsyncPushTokenRepository.

Verifies that the upsert() INSERT includes ``created_at`` so that the NOT NULL
constraint on ``user_fcm_tokens.created_at`` is never violated even when the
physical database column lacks a server-side DEFAULT (see PYTHON-K3).
"""

from unittest.mock import patch
from uuid import UUID

import pytest

from src.domain.model.notification.enums import DeviceType
from src.infra.repositories.push_token_repository import AsyncPushTokenRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal async session stub that records execute / flush calls."""

    def __init__(self):
        self.executed = []
        self.flushed = 0

    async def execute(self, statement):
        self.executed.append(statement)

    async def flush(self):
        self.flushed += 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_includes_created_at():
    """The compiled INSERT must contain 'created_at' in its column list.

    Without this column the DB raises a NOT NULL violation on rows whose
    physical column has no DEFAULT now() (PYTHON-K3).
    """
    session = _FakeSession()
    repo = AsyncPushTokenRepository(session)

    with patch(
        "src.infra.repositories.push_token_repository.uuid4",
        return_value=UUID("00000000-0000-0000-0000-000000000001"),
    ):
        await repo.upsert(
            user_id="user-abc",
            token="fcm-token-xyz",
            device_type=DeviceType.IOS,
        )

    assert session.flushed == 1, "flush() must be called after execute()"
    assert len(session.executed) == 1

    # Compile the SQLAlchemy statement to a SQL string and check the columns.
    stmt = session.executed[0]
    from sqlalchemy.dialects import postgresql

    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": False},
    )
    sql = str(compiled)

    assert "created_at" in sql, (
        "INSERT statement must include 'created_at'; without it the DB raises "
        "NotNullViolationError when the column has no server-side DEFAULT."
    )
    assert "last_registered_at" in sql
    assert "ON CONFLICT" in sql.upper()


@pytest.mark.asyncio
async def test_upsert_conflict_path_does_not_set_created_at():
    """The ON CONFLICT … DO UPDATE clause must NOT overwrite created_at.

    Overwriting it on conflict would reset the original creation timestamp.
    """
    session = _FakeSession()
    repo = AsyncPushTokenRepository(session)

    with patch(
        "src.infra.repositories.push_token_repository.uuid4",
        return_value=UUID("00000000-0000-0000-0000-000000000002"),
    ):
        await repo.upsert(
            user_id="user-abc",
            token="fcm-token-xyz",
            device_type=DeviceType.ANDROID,
        )

    stmt = session.executed[0]
    from sqlalchemy.dialects import postgresql

    compiled = stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": False},
    )
    sql = str(compiled)

    # The DO UPDATE SET block appears after ON CONFLICT (fcm_token).
    # created_at must only appear in the VALUES part, not in the SET block.
    on_conflict_idx = sql.upper().index("ON CONFLICT")
    set_block = sql[on_conflict_idx:]
    assert "created_at" not in set_block, (
        "DO UPDATE SET must not include created_at — that would reset "
        "the original registration timestamp on every re-registration."
    )


@pytest.mark.asyncio
async def test_upsert_device_type_uses_string_value():
    """device_type must be stored as its string value, not the enum name."""
    session = _FakeSession()
    repo = AsyncPushTokenRepository(session)

    with patch(
        "src.infra.repositories.push_token_repository.uuid4",
        return_value=UUID("00000000-0000-0000-0000-000000000003"),
    ):
        await repo.upsert(
            user_id="user-abc",
            token="fcm-token-android",
            device_type=DeviceType.ANDROID,
        )

    stmt = session.executed[0]
    # Inspect the bound parameters to confirm 'android' (not 'ANDROID') is used.
    params = stmt.compile(
        dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
    ).params
    assert params.get("device_type") == "android", (
        "device_type must be stored as the enum's string value ('android'), "
        "not its Python name ('ANDROID')."
    )

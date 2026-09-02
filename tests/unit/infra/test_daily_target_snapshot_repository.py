"""Write-once semantics for daily target snapshot repository."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.infra.repositories.daily_target_snapshot_repository_async import (
    AsyncDailyTargetSnapshotRepository,
)


@pytest.mark.asyncio
async def test_insert_if_absent_compiles_to_on_conflict_do_nothing():
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    repo = AsyncDailyTargetSnapshotRepository(session)
    await repo.insert_if_absent("user-1", date(2026, 9, 1), 1850.0)
    await repo.insert_if_absent("user-1", date(2026, 9, 1), 9999.0)
    assert session.execute.await_count == 2
    dialect = postgresql.dialect()
    for call in session.execute.await_args_list:
        compiled = str(
            call.args[0].compile(dialect=dialect, compile_kwargs={"literal_binds": True})
        ).upper()
        assert "ON CONFLICT" in compiled
        assert "DO NOTHING" in compiled
        assert "DO UPDATE" not in compiled

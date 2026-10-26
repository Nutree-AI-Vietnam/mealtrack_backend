"""SQL issued by the weekly recap repository, compiled for PostgreSQL."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from src.domain.model.weekly_recap import WeeklyRecap
from src.infra.database.models.weekly_recap import WeeklyRecapORM
from src.infra.repositories.weekly_recap_repository_async import (
    AsyncWeeklyRecapRepository,
)

WEEK = date(2026, 10, 5)
CREATED = datetime(2026, 10, 12, 1, 0, tzinfo=UTC)


class _Result:
    def __init__(
        self, scalar: Any = None, rows: list[Any] | None = None, rowcount: int = 0
    ):
        self._scalar = scalar
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, result: _Result):
        self.result = result
        self.statements: list[Any] = []
        self.flushes = 0

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return self.result

    async def flush(self) -> None:
        self.flushes += 1


def _sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _recap(**changes: Any) -> WeeklyRecap:
    fields: dict[str, Any] = {
        "user_id": "user-1",
        "week_start": WEEK,
        "case_id": "steady",
        "locale": "vi",
        "stats": {"week_no": 3},
        "payload": {"case_id": "steady"},
        "created_at": CREATED,
    }
    fields.update(changes)
    return WeeklyRecap(**fields)


@pytest.mark.asyncio
async def test_get_reads_one_week_for_one_user() -> None:
    session = _Session(_Result())

    recap = await AsyncWeeklyRecapRepository(session).get("user-1", WEEK)

    assert recap is None
    sql = _sql(session.statements[0])
    assert "weekly_recaps.user_id = 'user-1'" in sql
    assert "weekly_recaps.week_start = '2026-10-05'" in sql


@pytest.mark.asyncio
async def test_get_maps_the_row() -> None:
    row = WeeklyRecapORM(
        user_id="user-1",
        week_start=WEEK,
        case_id="steady",
        locale="vi",
        stats={"week_no": 3},
        payload={"case_id": "steady"},
        created_at=CREATED,
    )

    recap = await AsyncWeeklyRecapRepository(_Session(_Result(row))).get("user-1", WEEK)

    assert recap == _recap()


@pytest.mark.asyncio
@pytest.mark.parametrize(("returned", "inserted"), [(WEEK, True), (None, False)])
async def test_insert_never_overwrites_a_stored_week(
    returned: date | None, inserted: bool
) -> None:
    session = _Session(_Result(returned))

    result = await AsyncWeeklyRecapRepository(session).insert_if_absent(_recap())

    assert result is inserted
    assert session.flushes == 1
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("INSERT INTO weekly_recaps")
    assert compiled.params["stats"] == {"week_no": 3}
    assert "ON CONFLICT (user_id, week_start) DO NOTHING" in sql
    assert "RETURNING weekly_recaps.week_start" in sql
    assert "DO UPDATE" not in sql


@pytest.mark.asyncio
async def test_insert_stamps_created_at_when_missing() -> None:
    session = _Session(_Result(WEEK))

    await AsyncWeeklyRecapRepository(session).insert_if_absent(_recap(created_at=None))

    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    assert params["created_at"] is not None


@pytest.mark.asyncio
async def test_count_cases_before_groups_earlier_weeks_by_story() -> None:
    session = _Session(_Result(rows=[("perfect", 2), ("first_week", 1)]))

    counts = await AsyncWeeklyRecapRepository(session).count_cases_before(
        "user-1", WEEK
    )

    assert counts == {"perfect": 2, "first_week": 1}
    sql = _sql(session.statements[0])
    assert "weekly_recaps.user_id = 'user-1'" in sql
    assert "weekly_recaps.week_start < '2026-10-05'" in sql
    assert "GROUP BY weekly_recaps.case_id" in sql


@pytest.mark.asyncio
async def test_delete_for_user_removes_only_that_users_weeks() -> None:
    session = _Session(_Result(rowcount=3))

    deleted = await AsyncWeeklyRecapRepository(session).delete_for_user("user-1")

    assert deleted == 3
    sql = _sql(session.statements[0])
    assert sql.startswith("DELETE FROM weekly_recaps")
    assert "WHERE weekly_recaps.user_id = 'user-1'" in sql

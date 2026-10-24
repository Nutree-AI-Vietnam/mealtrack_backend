"""Async repository for write-once weekly recaps."""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.weekly_recap import WeeklyRecap
from src.domain.ports.weekly_recap_repository_port import WeeklyRecapRepositoryPort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.weekly_recap import WeeklyRecapORM


class AsyncWeeklyRecapRepository(WeeklyRecapRepositoryPort):
    """Never updates a stored recap. Callers commit."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: str, week_start: date) -> WeeklyRecap | None:
        result = await self.session.execute(
            select(WeeklyRecapORM).where(
                WeeklyRecapORM.user_id == user_id,
                WeeklyRecapORM.week_start == week_start,
            )
        )
        row = result.scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def insert_if_absent(self, recap: WeeklyRecap) -> bool:
        stmt = (
            pg_insert(WeeklyRecapORM)
            .values(
                user_id=recap.user_id,
                week_start=recap.week_start,
                case_id=recap.case_id,
                locale=recap.locale,
                stats=recap.stats,
                payload=recap.payload,
                created_at=recap.created_at or utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["user_id", "week_start"])
            .returning(WeeklyRecapORM.week_start)
        )
        result = await self.session.execute(stmt)
        inserted = result.scalar_one_or_none() is not None
        await self.session.flush()
        return inserted

    async def delete_for_user(self, user_id: str) -> int:
        result = await self.session.execute(
            delete(WeeklyRecapORM).where(WeeklyRecapORM.user_id == user_id)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def count_cases_before(self, user_id: str, before: date) -> dict[str, int]:
        result = await self.session.execute(
            select(WeeklyRecapORM.case_id, func.count())
            .where(
                WeeklyRecapORM.user_id == user_id,
                WeeklyRecapORM.week_start < before,
            )
            .group_by(WeeklyRecapORM.case_id)
        )
        return {str(case_id): int(count) for case_id, count in result.all()}


def _to_domain(row: WeeklyRecapORM) -> WeeklyRecap:
    return WeeklyRecap(
        user_id=str(row.user_id),
        week_start=row.week_start,
        case_id=str(row.case_id),
        locale=str(row.locale),
        stats=dict(row.stats or {}),
        payload=dict(row.payload or {}),
        created_at=row.created_at,
    )

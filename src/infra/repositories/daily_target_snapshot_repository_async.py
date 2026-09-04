"""Async repository for write-once daily target snapshots."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.daily_target_snapshot import DailyTargetSnapshotORM


class AsyncDailyTargetSnapshotRepository:
    """Never updates a stored snapshot. Callers must not commit."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_if_absent(
        self, user_id: str, target_date: date, adjusted_calories: float
    ) -> None:
        stmt = (
            pg_insert(DailyTargetSnapshotORM)
            .values(
                user_id=user_id,
                date=target_date,
                adjusted_target_calories=adjusted_calories,
                created_at=utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["user_id", "date"])
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def find_calories_by_range(
        self, user_id: str, start_date: date, end_date: date
    ) -> dict[date, float]:
        result = await self.session.execute(
            select(
                DailyTargetSnapshotORM.date,
                DailyTargetSnapshotORM.adjusted_target_calories,
            ).where(
                DailyTargetSnapshotORM.user_id == user_id,
                DailyTargetSnapshotORM.date >= start_date,
                DailyTargetSnapshotORM.date <= end_date,
            )
        )
        return {row.date: float(row.adjusted_target_calories) for row in result.all()}

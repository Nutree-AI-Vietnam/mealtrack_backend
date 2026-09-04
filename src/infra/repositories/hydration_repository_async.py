"""Async hydration entry repository."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.hydration import HydrationEntry
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros
from src.domain.model.nutrition.micros_ops import (
    mapping_from_micros,
    merge_micros,
    micros_from_mapping,
)
from src.domain.utils.timezone_utils import get_zone_info
from src.infra.database.models.hydration_entry import HydrationEntryORM


def _local_day_range(
    date_obj: date,
    user_timezone: str | None,
) -> tuple[datetime, datetime]:
    tz = get_zone_info(user_timezone) if user_timezone else timezone.utc  # noqa: UP017
    start_dt = datetime.combine(date_obj, datetime.min.time(), tzinfo=tz).astimezone(
        timezone.utc  # noqa: UP017
    )
    return start_dt, start_dt + timedelta(days=1)


def _date_expr(user_timezone: str | None):
    if user_timezone and user_timezone != "UTC":
        return func.date(func.timezone(user_timezone, HydrationEntryORM.logged_at))
    return func.date(HydrationEntryORM.logged_at)


def _orm_to_domain(row: HydrationEntryORM | None) -> HydrationEntry | None:
    if row is None:
        return None
    return HydrationEntry(
        id=row.id,
        user_id=row.user_id,
        drink_id=row.drink_id,
        drink_name_snapshot=row.drink_name_snapshot,
        emoji_snapshot=row.emoji_snapshot,
        volume_ml=row.volume_ml,
        credited_ml=row.credited_ml,
        protein_g=row.protein_g,
        carbs_g=row.carbs_g,
        fat_g=row.fat_g,
        fiber_g=row.fiber_g,
        sugar_g=row.sugar_g,
        logged_at=row.logged_at,
        source=row.source,
        legacy_meal_id=row.legacy_meal_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        image_url=row.image_url,
        micros=micros_from_mapping(getattr(row, "micros", None)),
    )


def _domain_to_orm(entry: HydrationEntry) -> HydrationEntryORM:
    return HydrationEntryORM(
        id=entry.id,
        user_id=entry.user_id,
        drink_id=entry.drink_id,
        drink_name_snapshot=entry.drink_name_snapshot,
        emoji_snapshot=entry.emoji_snapshot,
        volume_ml=entry.volume_ml,
        credited_ml=entry.credited_ml,
        protein_g=entry.protein_g,
        carbs_g=entry.carbs_g,
        fat_g=entry.fat_g,
        fiber_g=entry.fiber_g,
        sugar_g=entry.sugar_g,
        logged_at=entry.logged_at,
        source=entry.source,
        legacy_meal_id=entry.legacy_meal_id,
        image_url=entry.image_url,
        micros=mapping_from_micros(entry.micros),
    )


class AsyncHydrationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, entry: HydrationEntry) -> HydrationEntry:
        db = _domain_to_orm(entry)
        self.session.add(db)
        await self.session.flush()
        await self.session.refresh(db)
        return _orm_to_domain(db)

    async def find_by_id_or_legacy_meal_id(
        self,
        user_id: str,
        entry_id: str,
    ) -> HydrationEntry | None:
        result = await self.session.execute(
            select(HydrationEntryORM).where(
                HydrationEntryORM.user_id == user_id,
                or_(
                    HydrationEntryORM.id == entry_id,
                    HydrationEntryORM.legacy_meal_id == entry_id,
                ),
            )
        )
        return _orm_to_domain(result.scalars().first())

    async def find_by_date(
        self,
        date_obj: date,
        user_id: str,
        user_timezone: str | None = None,
    ) -> list[HydrationEntry]:
        start_dt, end_dt = _local_day_range(date_obj, user_timezone)
        result = await self.session.execute(
            select(HydrationEntryORM)
            .where(
                HydrationEntryORM.user_id == user_id,
                HydrationEntryORM.logged_at >= start_dt,
                HydrationEntryORM.logged_at < end_dt,
            )
            .order_by(HydrationEntryORM.logged_at.desc())
        )
        return [_orm_to_domain(row) for row in result.scalars().all()]

    async def find_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> list[HydrationEntry]:
        start_dt, _ = _local_day_range(start_date, user_timezone)
        _, end_dt = _local_day_range(end_date, user_timezone)
        result = await self.session.execute(
            select(HydrationEntryORM)
            .where(
                HydrationEntryORM.user_id == user_id,
                HydrationEntryORM.logged_at >= start_dt,
                HydrationEntryORM.logged_at < end_dt,
            )
            .order_by(HydrationEntryORM.logged_at.desc())
        )
        return [_orm_to_domain(row) for row in result.scalars().all()]

    async def sum_ml_for_date(
        self,
        date_obj: date,
        user_id: str,
        user_timezone: str | None = None,
    ) -> int:
        start_dt, end_dt = _local_day_range(date_obj, user_timezone)
        result = await self.session.execute(
            select(func.coalesce(func.sum(HydrationEntryORM.credited_ml), 0)).where(
                HydrationEntryORM.user_id == user_id,
                HydrationEntryORM.logged_at >= start_dt,
                HydrationEntryORM.logged_at < end_dt,
            )
        )
        return int(result.scalar_one() or 0)

    async def sum_ml_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> dict[date, int]:
        start_dt, _ = _local_day_range(start_date, user_timezone)
        _, end_dt = _local_day_range(end_date, user_timezone)
        date_expr = _date_expr(user_timezone)
        result = await self.session.execute(
            select(date_expr, func.coalesce(func.sum(HydrationEntryORM.credited_ml), 0))
            .where(
                HydrationEntryORM.user_id == user_id,
                HydrationEntryORM.logged_at >= start_dt,
                HydrationEntryORM.logged_at < end_dt,
            )
            .group_by(date_expr)
        )

        totals: dict[date, int] = {}
        for day_val, total in result.all():
            if isinstance(day_val, str):
                day_val = date.fromisoformat(day_val)
            totals[day_val] = int(total)
        return totals

    async def sum_macros_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> dict[date, Macros]:
        start_dt, _ = _local_day_range(start_date, user_timezone)
        _, end_dt = _local_day_range(end_date, user_timezone)
        date_expr = _date_expr(user_timezone)
        result = await self.session.execute(
            select(
                date_expr,
                func.coalesce(func.sum(HydrationEntryORM.protein_g), 0),
                func.coalesce(func.sum(HydrationEntryORM.carbs_g), 0),
                func.coalesce(func.sum(HydrationEntryORM.fat_g), 0),
                func.coalesce(func.sum(HydrationEntryORM.fiber_g), 0),
            )
            .where(
                HydrationEntryORM.user_id == user_id,
                HydrationEntryORM.logged_at >= start_dt,
                HydrationEntryORM.logged_at < end_dt,
            )
            .group_by(date_expr)
        )
        totals: dict[date, Macros] = {}
        for day_val, protein, carbs, fat, fiber in result.all():
            if isinstance(day_val, str):
                day_val = date.fromisoformat(day_val)
            totals[day_val] = Macros(
                protein=float(protein or 0.0),
                carbs=float(carbs or 0.0),
                fat=float(fat or 0.0),
                fiber=float(fiber or 0.0),
            )
        return totals

    async def sum_micros_by_date_range(
        self,
        user_id: str,
        start_date: date,
        end_date: date,
        user_timezone: str | None = None,
    ) -> dict[date, Micros]:
        start_dt, _ = _local_day_range(start_date, user_timezone)
        _, end_dt = _local_day_range(end_date, user_timezone)
        date_expr = _date_expr(user_timezone)
        result = await self.session.execute(
            select(date_expr, HydrationEntryORM.micros).where(
                HydrationEntryORM.user_id == user_id,
                HydrationEntryORM.logged_at >= start_dt,
                HydrationEntryORM.logged_at < end_dt,
            )
        )
        totals: dict[date, Micros] = {}
        for day_val, raw in result.all():
            if isinstance(day_val, str):
                day_val = date.fromisoformat(day_val)
            part = micros_from_mapping(raw)
            if part is None:
                continue
            totals[day_val] = merge_micros(totals.get(day_val), part)
        return totals

    async def fetch_journey_progress_hydration(
        self,
        user_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[dict]:
        result = await self.session.execute(
            select(
                HydrationEntryORM.logged_at,
                HydrationEntryORM.drink_name_snapshot,
                HydrationEntryORM.credited_ml,
                HydrationEntryORM.protein_g,
                HydrationEntryORM.carbs_g,
                HydrationEntryORM.fat_g,
                HydrationEntryORM.fiber_g,
            )
            .where(
                HydrationEntryORM.user_id == user_id,
                HydrationEntryORM.logged_at >= start_utc,
                HydrationEntryORM.logged_at < end_utc,
            )
            .order_by(HydrationEntryORM.logged_at.asc())
        )
        return [
            {
                "logged_at": logged_at,
                "label": label or "Hydration",
                "hydration_ml": int(credited_ml or 0),
                "calories": round(
                    float(protein_g or 0.0) * 4
                    + max(0.0, float(carbs_g or 0.0) - float(fiber_g or 0.0)) * 4
                    + float(fiber_g or 0.0) * 2
                    + float(fat_g or 0.0) * 9,
                    1,
                ),
                "protein_g": float(protein_g or 0.0),
            }
            for logged_at, label, credited_ml, protein_g, carbs_g, fat_g, fiber_g in result.all()
        ]

    async def delete_by_id_or_legacy_meal_id(self, user_id: str, entry_id: str) -> bool:
        result = await self.session.execute(
            delete(HydrationEntryORM).where(
                HydrationEntryORM.user_id == user_id,
                or_(
                    HydrationEntryORM.id == entry_id,
                    HydrationEntryORM.legacy_meal_id == entry_id,
                ),
            )
        )
        return result.rowcount > 0

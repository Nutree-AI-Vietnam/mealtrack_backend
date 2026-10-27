"""Async vacation repository. Never calls session.commit()."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.vacation.vacation import Vacation
from src.infra.database.models.vacation.vacation import VacationORM
from src.infra.mappers.vacation_mapper import (
    vacation_domain_to_orm,
    vacation_orm_to_domain,
)


class AsyncVacationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_by_user(self, user_id: str) -> list[Vacation]:
        result = await self.session.execute(
            select(VacationORM)
            .where(VacationORM.user_id == user_id)
            .order_by(VacationORM.start_date.desc())
        )
        return [vacation_orm_to_domain(row) for row in result.scalars().all()]

    async def save(self, vacation: Vacation) -> None:
        existing = await self.session.get(VacationORM, vacation.vacation_id)
        if existing is None:
            self.session.add(vacation_domain_to_orm(vacation))
        else:
            existing.start_date = vacation.start_date
            existing.end_date = vacation.end_date
            existing.ended_on = vacation.ended_on
            existing.frozen_calories = vacation.frozen_calories
            existing.frozen_protein = vacation.frozen_protein
            existing.frozen_carbs = vacation.frozen_carbs
            existing.frozen_fat = vacation.frozen_fat
        await self.session.flush()

    async def delete(self, vacation_id: str) -> None:
        existing = await self.session.get(VacationORM, vacation_id)
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

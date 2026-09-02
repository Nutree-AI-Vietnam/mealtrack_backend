"""Async repository for favorite meals backed by SQLAlchemy asyncpg."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.model.meal import Meal
from src.domain.ports.favorite_meal_repository_port import FavoriteMealRepositoryPort
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.meal.favorite_meal import FavoriteMealORM
from src.infra.database.models.meal.meal import MealORM
from src.infra.database.models.nutrition.nutrition import NutritionORM
from src.infra.mappers.meal_mapper import meal_orm_to_domain

logger = logging.getLogger(__name__)


class AsyncFavoriteMealRepository(FavoriteMealRepositoryPort):
    """SQLAlchemy AsyncSession implementation of FavoriteMealRepositoryPort."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def favorite(
        self,
        user_id: str,
        meal_id: str,
        favorited_at: datetime | None = None,
    ) -> bool:
        """Add meal to user's favorites idempotently."""
        now = favorited_at or utc_now()
        stmt = (
            pg_insert(FavoriteMealORM)
            .values(
                user_id=user_id,
                meal_id=meal_id,
                favorited_at=now,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "meal_id"])
            .returning(FavoriteMealORM.meal_id)
        )
        result = await self.session.execute(stmt)
        inserted = result.scalar_one_or_none()
        return inserted is not None

    async def unfavorite(self, user_id: str, meal_id: str) -> bool:
        """Remove meal from user's favorites idempotently."""
        stmt = delete(FavoriteMealORM).where(
            FavoriteMealORM.user_id == user_id,
            FavoriteMealORM.meal_id == meal_id,
        )
        result = await self.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount and rowcount > 0)

    async def is_favorite(self, user_id: str, meal_id: str) -> bool:
        """Check if meal is favorited by user."""
        stmt = (
            select(FavoriteMealORM.meal_id)
            .where(
                FavoriteMealORM.user_id == user_id,
                FavoriteMealORM.meal_id == meal_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def count_favorites(self, user_id: str) -> int:
        """Return the number of meals currently favorited by the user."""
        stmt = (
            select(func.count())
            .select_from(FavoriteMealORM)
            .where(FavoriteMealORM.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def filter_favorited_meal_ids(
        self,
        user_id: str,
        meal_ids: Sequence[str],
    ) -> set[str]:
        """Return set of meal IDs favorited by user."""
        if not meal_ids:
            return set()
        stmt = select(FavoriteMealORM.meal_id).where(
            FavoriteMealORM.user_id == user_id,
            FavoriteMealORM.meal_id.in_(list(meal_ids)),
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    async def list_favorite_meals(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[tuple[Meal, datetime]]:
        """Return user's favorited meals ordered newest-favorited first.

        Includes both active and inactive source meals.
        """
        stmt = (
            select(FavoriteMealORM)
            .options(
                selectinload(FavoriteMealORM.meal).selectinload(MealORM.image),
                selectinload(FavoriteMealORM.meal).selectinload(MealORM.translations),
                selectinload(FavoriteMealORM.meal)
                .selectinload(MealORM.nutrition)
                .selectinload(NutritionORM.food_items),
                selectinload(FavoriteMealORM.meal).selectinload(
                    MealORM.instruction_steps
                ),
            )
            .where(FavoriteMealORM.user_id == user_id)
            .order_by(FavoriteMealORM.favorited_at.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        rows = result.unique().scalars().all()
        favorite_list: list[tuple[Meal, datetime]] = []
        for row in rows:
            if row.meal is not None:
                domain_meal = meal_orm_to_domain(row.meal)
                favorite_list.append((domain_meal, cast(datetime, row.favorited_at)))
        return favorite_list

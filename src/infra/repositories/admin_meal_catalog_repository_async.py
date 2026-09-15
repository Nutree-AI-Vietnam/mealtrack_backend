"""Admin read/update repository for curated catalog meals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domain.model.meal_recommendation.catalog_recipe import CatalogMeal
from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.database.models.meal_recommendation import (
    MealCatalogIngredientORM,
    MealCatalogORM,
)
from src.infra.repositories.catalog_recipe_repository_async import _meal_to_domain


@dataclass(frozen=True)
class AdminCatalogMealProjection:
    meal: CatalogMeal
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class AdminCatalogMealPage:
    items: tuple[AdminCatalogMealProjection, ...]
    total: int


class AsyncAdminMealCatalogRepository:
    """Repository backing protected admin catalog viewer endpoints."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_meals(
        self,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        cuisine: str | None = None,
        meal_type: str | None = None,
        has_image: bool | None = None,
        is_active: bool | None = None,
    ) -> AdminCatalogMealPage:
        filters = _catalog_filters(
            q=q,
            cuisine=cuisine,
            meal_type=meal_type,
            has_image=has_image,
            is_active=is_active,
        )
        count_result = await self._session.execute(
            select(func.count()).select_from(MealCatalogORM).where(*filters)
        )
        total = int(count_result.scalar_one() or 0)

        result = await self._session.execute(
            select(MealCatalogORM)
            .where(*filters)
            .options(_load_options())
            .order_by(MealCatalogORM.name, MealCatalogORM.id)
            .limit(limit)
            .offset(offset)
        )
        rows = result.scalars().unique().all()
        return AdminCatalogMealPage(
            items=tuple(_projection(row) for row in rows),
            total=total,
        )

    async def get_meal(self, catalog_id: str) -> AdminCatalogMealProjection | None:
        result = await self._session.execute(
            select(MealCatalogORM)
            .where(MealCatalogORM.id == catalog_id)
            .options(_load_options())
        )
        row = result.scalar_one_or_none()
        return _projection(row) if row else None

    async def get_meal_row(self, catalog_id: str) -> MealCatalogORM | None:
        result = await self._session.execute(
            select(MealCatalogORM)
            .where(MealCatalogORM.id == catalog_id)
            .options(selectinload(MealCatalogORM.ingredients))
        )
        return result.scalar_one_or_none()

    async def set_missing_image_url(self, catalog_id: str, image_url: str) -> bool:
        result = await self._session.execute(
            update(MealCatalogORM)
            .where(MealCatalogORM.id == catalog_id)
            .where(_missing_image_filter())
            .values(image_url=image_url, updated_at=func.now())
        )
        await self._session.flush()
        return bool(result.rowcount)

    async def commit(self) -> None:
        await self._session.commit()


def _catalog_filters(
    *,
    q: str | None,
    cuisine: str | None,
    meal_type: str | None,
    has_image: bool | None,
    is_active: bool | None,
):
    filters = []
    query = (q or "").strip()
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(
                MealCatalogORM.name.ilike(pattern),
                MealCatalogORM.catalog_key.ilike(pattern),
                MealCatalogORM.cuisine.ilike(pattern),
            )
        )
    if cuisine:
        filters.append(MealCatalogORM.cuisine == cuisine.strip())
    if meal_type:
        filters.append(_meal_type_column(meal_type).is_(True))
    if has_image is True:
        filters.append(
            and_(
                MealCatalogORM.image_url.is_not(None),
                func.trim(MealCatalogORM.image_url) != "",
            )
        )
    elif has_image is False:
        filters.append(_missing_image_filter())
    if is_active is not None:
        filters.append(MealCatalogORM.is_active.is_(is_active))
    return tuple(filters)


def _missing_image_filter():
    return or_(
        MealCatalogORM.image_url.is_(None), func.trim(MealCatalogORM.image_url) == ""
    )


def _meal_type_column(meal_type: str):
    if meal_type == "breakfast":
        return MealCatalogORM.breakfast_eligible
    if meal_type == "lunch":
        return MealCatalogORM.lunch_eligible
    if meal_type == "dinner":
        return MealCatalogORM.dinner_eligible
    if meal_type == "snack":
        return MealCatalogORM.snack_eligible
    raise ValueError(f"unsupported meal_type: {meal_type}")


def _load_options():
    return (
        selectinload(MealCatalogORM.ingredients)
        .selectinload(MealCatalogIngredientORM.food_reference)
        .selectinload(FoodReferenceModel.serving_size_rows)
    )


def _projection(row: MealCatalogORM) -> AdminCatalogMealProjection:
    return AdminCatalogMealProjection(
        meal=_meal_to_domain(row),
        created_at=cast(datetime | None, row.created_at),
        updated_at=cast(datetime | None, row.updated_at),
    )

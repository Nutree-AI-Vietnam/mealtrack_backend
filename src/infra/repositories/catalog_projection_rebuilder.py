"""Bounded, restartable projection reconciliation; callers own the transaction."""

from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionAllergenORM,
    MealCatalogProjectionORM,
)
from src.infra.database.models.meal_recommendation.catalog_recipe import MealCatalogORM
from src.infra.repositories.catalog_projection_builder import projection_values
from src.infra.repositories.catalog_publication_fence import catalog_publication_version


class CatalogProjectionRebuilder:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def rebuild(self, ids: Iterable[str]) -> int:
        """Exclusive fence prevents publication or dependency changes mid-build."""
        from src.infra.repositories.catalog_recipe_repository_async import (
            _catalog_meal_load_options,
            _meal_to_domain,
        )

        ids = tuple(dict.fromkeys(ids))
        if len(ids) > 500:
            raise ValueError(
                "Catalog projection batch must contain at most 500 recipes"
            )
        if not ids:
            return 0
        await catalog_publication_version(self.session, shared=False)
        await self.session.flush()
        result = await self.session.execute(
            select(MealCatalogORM)
            .where(MealCatalogORM.id.in_(ids))
            .options(
                *_catalog_meal_load_options(include_nutrients=True),
                selectinload(MealCatalogORM.steps),
            )
            .execution_options(populate_existing=True)
        )
        rows = result.scalars().unique().all()
        values = []
        allergen_links = []
        for row in rows:
            value, links = projection_values(row, _meal_to_domain(row))
            values.append(value)
            allergen_links.extend(links)
        await self.session.execute(
            delete(MealCatalogProjectionAllergenORM).where(
                MealCatalogProjectionAllergenORM.catalog_meal_id.in_(ids)
            )
        )
        if values:
            sqlite = self.session.get_bind().dialect.name == "sqlite"
            insert = sqlite_insert if sqlite else pg_insert
            statement = insert(MealCatalogProjectionORM).values(values)
            statement = statement.on_conflict_do_update(
                index_elements=["catalog_meal_id"],
                set_={
                    key: getattr(statement.excluded, key)
                    for key in values[0]
                    if key != "catalog_meal_id"
                }
                | {"updated_at": func.now() if sqlite else func.clock_timestamp()},
            )
            await self.session.execute(statement)
            self.session.add_all(allergen_links)
        await self.session.flush()
        return len(values)

    async def reconcile_page(
        self, *, after_id: str | None = None, limit: int = 100
    ) -> tuple[int, str | None]:
        """Persist cursor after commit and repeat; no OFFSET or unbounded graph."""
        if not 1 <= limit <= 500:
            raise ValueError("Catalog projection page size must be between 1 and 500")
        stmt = select(MealCatalogORM.id).order_by(MealCatalogORM.id).limit(limit)
        if after_id is not None:
            stmt = stmt.where(MealCatalogORM.id > after_id)
        ids = tuple((await self.session.execute(stmt)).scalars().all())
        count = await self.rebuild(ids)
        return count, ids[-1] if ids else None

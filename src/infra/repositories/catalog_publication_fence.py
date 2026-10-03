"""Transaction-scoped publication fencing, shared by readers and publishers."""

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.meal_recommendation.catalog_selection_features import (
    CatalogPublicationVersion,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    CatalogPublicationVersionORM,
)
from src.planner_observability import planner_timed


@planner_timed("lock")
async def catalog_publication_version(
    session: AsyncSession, *, shared: bool | None = None
) -> CatalogPublicationVersion:
    stmt = select(CatalogPublicationVersionORM)
    if shared is not None:
        stmt = stmt.with_for_update(read=shared)
    row = (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()
    if row is None:
        raise RuntimeError(
            "Catalog publication fence is not initialized; apply the catalog projection migration"
        )
    return CatalogPublicationVersion(
        cast(int, row.selection),
        cast(int, row.ingredients),
        cast(int, row.translation),
        cast(int, row.enrichment),
    )

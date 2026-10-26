"""Transaction boundaries for admin catalog writes.

The admin catalog collaborators (seed importer, reference reviewer, catalog
repository) share the request's session. These functions own its commit and
rollback so routes only validate requests and shape responses.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.services.catalog_food_reference_review_service import (
    CatalogFoodReferenceApproval,
    CatalogFoodReferenceReviewService,
)
from src.app.services.catalog_meal_seed_import_service import (
    CatalogMealSeedImporter,
    CatalogSeedCandidateEnrichmentSummary,
    CatalogSeedImportSummary,
)


class CatalogImageWriter(Protocol):
    async def set_image_url(
        self, catalog_id: str, image_url: str, *, force: bool = False
    ) -> bool: ...


async def approve_catalog_food_reference(
    db: AsyncSession,
    reviewer: CatalogFoodReferenceReviewService,
    food_reference_id: int,
) -> CatalogFoodReferenceApproval | None:
    """Commit an admin approval; ``None`` means the reference no longer exists."""
    approval = await reviewer.approve(food_reference_id)
    if approval is not None:
        await db.commit()
    return approval


async def enrich_catalog_candidates(
    db: AsyncSession,
    importer: CatalogMealSeedImporter,
    manifest: dict[str, Any],
) -> CatalogSeedCandidateEnrichmentSummary:
    """Commit cached FatSecret candidates without publishing catalog recipes."""
    summary = await importer.enrich_missing_candidates(manifest)
    await db.commit()
    return summary


async def finish_catalog_import(
    db: AsyncSession, summary: CatalogSeedImportSummary
) -> bool:
    """Commit a successful applied import, or roll back a failed one."""
    if not summary.is_successful:
        await db.rollback()
        return False
    await db.commit()
    return True


async def set_catalog_meal_image(
    db: AsyncSession,
    repository: CatalogImageWriter,
    catalog_id: str,
    image_url: str,
    *,
    force: bool,
) -> bool:
    """Commit the image URL; ``False`` means another write got there first."""
    if not await repository.set_image_url(catalog_id, image_url, force=force):
        return False
    await db.commit()
    return True

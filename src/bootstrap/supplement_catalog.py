"""Composition root for the curated supplement catalog and its review queue.

Each call is one transaction; the admin routes and the seed script share them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.exc import IntegrityError

from src.app.services.supplement_catalog_import import (
    SupplementImportSummary,
    import_catalog,
)
from src.app.services.supplement_catalog_review import (
    ReviewAction,
    ReviewOutcome,
    review_product,
)
from src.domain.model.supplement.catalog_review import SupplementReviewConflictError
from src.domain.ports.supplement_catalog_port import (
    ReviewItem,
    ReviewKind,
    SupplementCatalogPort,
)
from src.infra.database.uow_async import AsyncUnitOfWork


class SupplementCatalogUnavailableError(RuntimeError):
    """The supplement tables are not migrated yet."""


async def import_supplement_catalog(
    records: Sequence[object], *, dry_run: bool = False
) -> SupplementImportSummary:
    """Import curated records; a dry run reports the same counts and rolls back."""
    async with AsyncUnitOfWork() as uow:
        catalog = await _catalog(uow)
        summary = await import_catalog(catalog, records)
        if dry_run:
            await uow.rollback()
        else:
            await uow.commit()
        return summary


async def supplement_review_queue(
    limit: int, offset: int, kind: ReviewKind | None = None
) -> tuple[list[ReviewItem], int]:
    async with AsyncUnitOfWork() as uow:
        catalog = await _catalog(uow)
        return await catalog.review_queue(limit, offset, kind)


async def review_supplement(
    product_id: str,
    action: ReviewAction,
    reviewer: str,
    changes: Mapping[str, Any] | None = None,
) -> ReviewOutcome:
    try:
        async with AsyncUnitOfWork() as uow:
            catalog = await _catalog(uow)
            outcome = await review_product(
                catalog, product_id, action, reviewer, changes
            )
            await uow.commit()
            return outcome
    except IntegrityError as error:
        # Two approvals of one barcode, or the person listing the same product.
        raise SupplementReviewConflictError(
            "another change to this barcode or list landed first; reload and retry"
        ) from error


async def _catalog(uow: AsyncUnitOfWork) -> SupplementCatalogPort:
    catalog = uow.supplement_catalog
    if not await catalog.schema_ready():
        raise SupplementCatalogUnavailableError("supplement tables are not migrated")
    return catalog

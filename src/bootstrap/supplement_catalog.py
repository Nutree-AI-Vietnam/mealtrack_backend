"""Composition root for the curated supplement catalog and its review queue.

Each call is one transaction; the admin routes and the seed script share them.
"""

from __future__ import annotations

import logging
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
    review_page,
    review_product,
)
from src.app.services.supplement_product_photo import (
    StoredPhoto,
    delete_photo,
    point_at_photo,
    store_photo,
)
from src.domain.model.supplement import SupplementProduct
from src.domain.model.supplement.catalog_review import SupplementReviewConflictError
from src.domain.ports.image_store_port import ImageStorePort
from src.domain.ports.supplement_catalog_port import (
    QueueStatus,
    ReviewItem,
    ReviewKind,
    SupplementCatalogPort,
)
from src.infra.database.uow_async import AsyncUnitOfWork

logger = logging.getLogger(__name__)

_UNIQUE_VIOLATION = "23505"


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
    limit: int,
    offset: int,
    kind: ReviewKind | None = None,
    q: str | None = None,
    status: QueueStatus = "pending_review",
    has_image: bool | None = None,
) -> tuple[list[tuple[ReviewItem, SupplementProduct | None]], int]:
    async with AsyncUnitOfWork() as uow:
        catalog = await _catalog(uow)
        return await review_page(catalog, limit, offset, kind, q, status, has_image)


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
        # Only a unique clash is a race worth retrying: two approvals of one
        # barcode, or the person listing the same product meanwhile.
        if _sqlstate(error) != _UNIQUE_VIOLATION:
            raise
        logger.warning(
            "Supplement review %s lost a race on %s",
            product_id,
            _constraint(error),
        )
        raise SupplementReviewConflictError(
            "another change to this barcode or list landed first; reload and retry"
        ) from error


async def set_supplement_photo(
    product_id: str, data: bytes, image_store: ImageStorePort
) -> ReviewItem:
    """Store the upload, point the catalog product at it, then drop the old photo."""
    photo = await store_photo(image_store, data)
    try:
        item, previous = await _point_at(product_id, photo)
    except Exception:
        await delete_photo(image_store, photo.image_id, reason="product update failed")
        raise
    if previous:
        await delete_photo(image_store, previous, reason="photo replaced")
    return item


async def remove_supplement_photo(
    product_id: str, image_store: ImageStorePort
) -> ReviewItem:
    """Take the photo off a catalog product, then delete it from the store."""
    item, previous = await _point_at(product_id, None)
    if previous:
        await delete_photo(image_store, previous, reason="photo removed")
    return item


async def _point_at(
    product_id: str, photo: StoredPhoto | None
) -> tuple[ReviewItem, str | None]:
    async with AsyncUnitOfWork() as uow:
        catalog = await _catalog(uow)
        result = await point_at_photo(catalog, product_id, photo)
        await uow.commit()
        return result


async def _catalog(uow: AsyncUnitOfWork) -> SupplementCatalogPort:
    catalog = uow.supplement_catalog
    if not await catalog.schema_ready():
        raise SupplementCatalogUnavailableError("supplement tables are not migrated")
    return catalog


def _sqlstate(error: IntegrityError) -> str | None:
    orig = error.orig
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )


def _constraint(error: IntegrityError) -> str | None:
    cause = getattr(error.orig, "__cause__", None)
    return getattr(cause, "constraint_name", None) or getattr(
        error.orig, "constraint_name", None
    )

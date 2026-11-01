"""An admin's decision on a held-back catalog row or a person's label scan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from src.domain.model.supplement import (
    ReviewStatus,
    SupplementProduct,
    SupplementProductNotFoundError,
)
from src.domain.model.supplement.catalog_review import (
    SupplementNotReviewableError,
    promoted,
    require_reviewable,
    with_edits,
)
from src.domain.ports.supplement_catalog_port import (
    ReviewItem,
    SupplementCatalogPort,
)
from src.domain.utils.timezone_utils import utc_now


class ReviewAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


@dataclass(frozen=True)
class ReviewOutcome:
    """The row as reviewed; ``merged_into`` names the shared row a scan joined."""

    item: ReviewItem
    merged_into: str | None = None


async def review_product(
    catalog: SupplementCatalogPort,
    product_id: str,
    action: ReviewAction,
    reviewer: str,
    changes: Mapping[str, Any] | None = None,
) -> ReviewOutcome:
    """Edit, approve or reject a product waiting for review.

    Approving a held-back catalog row shares it, unless an approved product
    already has its barcode (then it is a duplicate to reject). Approving a
    person's scan shares their row in place, so their list and doses keep it;
    when an approved row already has its barcode, their list moves there and
    the scan stays theirs. A barcode on a catalog row still waiting must be
    decided there first. Rejecting hides a catalog row (and frees its
    barcodes) and leaves a scan private.
    """
    found = await catalog.review_item(product_id)
    if found is None:
        raise SupplementProductNotFoundError(product_id)
    require_reviewable(found.product)
    product = with_edits(found.product, changes) if changes else found.product

    if action is ReviewAction.EDIT:
        if product == found.product:
            # Saving nothing would still mark the row reviewed, out of re-imports.
            raise ValueError("there are no changes to save")
        return await _saved(catalog, found, product, reviewer)
    if action is ReviewAction.REJECT:
        rejected = replace(product, review_status=ReviewStatus.REJECTED)
        return await _saved(catalog, found, rejected, reviewer)
    match = await catalog.shared_with_barcode(product.barcodes, exclude_id=product.id)
    if match is not None and match.review_status is ReviewStatus.PENDING_REVIEW:
        # Deciding that row first keeps one shared row per barcode: approving it
        # makes this a duplicate, rejecting it frees the barcode.
        raise SupplementNotReviewableError(
            f"catalog row {match.name!r} ({match.id}) has this barcode and is"
            " waiting for review; review it first"
        )
    if product.shared:
        if match is not None:
            raise SupplementNotReviewableError(
                f"approved product {match.name!r} ({match.id}) already has this"
                " barcode; reject this row as a duplicate"
            )
        approved = replace(product, review_status=ReviewStatus.APPROVED)
        return await _saved(catalog, found, approved, reviewer)

    owner = product.owner_user_id or ""
    if match is not None:
        await catalog.move_list_rows(owner, product.id, match.id)
        kept = replace(product, review_status=ReviewStatus.PRIVATE)
        outcome = await _saved(catalog, found, kept, reviewer)
        return replace(outcome, merged_into=match.id)
    return await _saved(catalog, found, promoted(product), reviewer)


async def _saved(
    catalog: SupplementCatalogPort,
    found: ReviewItem,
    product: SupplementProduct,
    reviewer: str,
) -> ReviewOutcome:
    barcodes = await catalog.save_review(product, reviewer)
    return ReviewOutcome(
        item=replace(
            found,
            product=replace(product, barcodes=barcodes),
            reviewed_at=utc_now(),
            reviewed_by=reviewer,
        )
    )

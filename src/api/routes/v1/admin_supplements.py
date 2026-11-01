"""Admin endpoints for the curated supplement catalog and its review queue."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies.auth import require_admin_or_local
from src.api.schemas.response.admin_supplement_responses import (
    AdminSupplementBarcodeSkip,
    AdminSupplementImportRequest,
    AdminSupplementImportResponse,
    AdminSupplementInvalidRecord,
    AdminSupplementLabelRow,
    AdminSupplementReviewItem,
    AdminSupplementReviewListResponse,
    AdminSupplementReviewRequest,
    AdminSupplementReviewResponse,
)
from src.app.services.supplement_catalog_review import ReviewAction
from src.bootstrap.supplement_catalog import (
    SupplementCatalogUnavailableError,
    import_supplement_catalog,
    review_supplement,
    supplement_review_queue,
)
from src.domain.model.supplement import ProductSource, SupplementProductNotFoundError
from src.domain.model.supplement.catalog_review import SupplementNotReviewableError
from src.domain.ports.supplement_catalog_port import ReviewItem

router = APIRouter(prefix="/v1/admin/supplements", tags=["Admin Supplements"])


@router.post("/import", response_model=AdminSupplementImportResponse)
async def import_admin_supplements(
    request: AdminSupplementImportRequest,
    _admin: str = Depends(require_admin_or_local),
) -> AdminSupplementImportResponse:
    """Upsert curated records by key; ``dry_run`` reports the counts only."""
    try:
        summary = await import_supplement_catalog(
            request.records, dry_run=request.dry_run
        )
    except SupplementCatalogUnavailableError as error:
        raise _unavailable(error) from error
    return AdminSupplementImportResponse(
        applied=not request.dry_run,
        received=summary.received,
        held_back=summary.held_back,
        inserted=summary.inserted,
        updated=summary.updated,
        unchanged=summary.unchanged,
        kept_reviewed=summary.kept_reviewed,
        kept_approved=summary.kept_approved,
        invalid=[
            AdminSupplementInvalidRecord(record=i.record, error=i.error)
            for i in summary.invalid
        ],
        barcode_skips=[
            AdminSupplementBarcodeSkip(
                catalog_key=s.catalog_key, barcode=s.barcode, reason=s.reason
            )
            for s in summary.barcode_skips
        ],
    )


@router.get("/review", response_model=AdminSupplementReviewListResponse)
async def list_supplement_review(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    kind: Literal["catalog", "scan"] | None = Query(None),
    _admin: str = Depends(require_admin_or_local),
) -> AdminSupplementReviewListResponse:
    """Held-back catalog rows and people's label scans waiting for review."""
    try:
        items, total = await supplement_review_queue(limit, offset, kind)
    except SupplementCatalogUnavailableError as error:
        raise _unavailable(error) from error
    return AdminSupplementReviewListResponse(
        items=[_item(item) for item in items], total=total, limit=limit, offset=offset
    )


@router.post("/review/{product_id}", response_model=AdminSupplementReviewResponse)
async def review_admin_supplement(
    product_id: str,
    request: AdminSupplementReviewRequest,
    admin: str = Depends(require_admin_or_local),
) -> AdminSupplementReviewResponse:
    """Approve, edit or reject one product; ``changes`` apply first."""
    changes = (
        request.changes.model_dump(exclude_unset=True) if request.changes else None
    )
    try:
        outcome = await review_supplement(
            product_id, ReviewAction(request.action), admin, changes
        )
    except SupplementCatalogUnavailableError as error:
        raise _unavailable(error) from error
    except SupplementProductNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Supplement not found"
        ) from error
    except SupplementNotReviewableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return AdminSupplementReviewResponse(
        item=_item(outcome.item), merged_into=outcome.merged_into
    )


def _item(item: ReviewItem) -> AdminSupplementReviewItem:
    product = item.product
    return AdminSupplementReviewItem(
        id=product.id,
        # By origin, so a scan just approved still reads as a scan.
        kind="catalog" if product.source is ProductSource.CURATED else "scan",
        catalog_key=product.catalog_key,
        brand=product.brand,
        name=product.name,
        name_original=product.name_original,
        form=product.form.value,
        unit_label=product.unit_label,
        units_per_serving=product.units_per_serving,
        nutrients=product.nutrients.to_json(),
        label_rows=[
            AdminSupplementLabelRow(**row.to_json())  # type: ignore[arg-type]
            for row in product.label_rows
        ],
        barcodes=list(product.barcodes),
        origin_country=product.origin_country,
        sold_in_vn=product.sold_in_vn,
        is_medicine_in_vn=product.is_medicine_in_vn,
        macro_bearing=product.macro_bearing,
        source=product.source.value,
        source_name=product.source_name,
        source_url=product.source_url,
        label_image_url=product.label_image_url,
        reasons=list(item.reasons),
        review_status=product.review_status.value,
        reviewed_at=item.reviewed_at,
        reviewed_by=item.reviewed_by,
        created_at=product.created_at,
    )


def _unavailable(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
    )

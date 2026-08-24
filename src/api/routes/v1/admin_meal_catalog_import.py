"""Protected admin endpoints for importing the curated meal catalog."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.base_dependencies import (
    get_async_db,
    get_catalog_food_reference_review_service,
    get_catalog_meal_seed_importer,
    get_catalog_meal_snapshot_service,
)
from src.api.dependencies.auth import require_admin_or_local
from src.api.schemas.response.admin_meal_catalog_responses import (
    AdminMealCatalogApproveFoodReferenceRequest,
    AdminMealCatalogApproveFoodReferenceResponse,
    AdminMealCatalogEnrichmentResponse,
    AdminMealCatalogImportRequest,
    AdminMealCatalogImportResponse,
    AdminMealCatalogValidationResponse,
)
from src.app.services.catalog_food_reference_review_service import (
    CatalogFoodReferenceReviewService,
)
from src.app.services.catalog_meal_seed_import_service import CatalogMealSeedImporter
from src.domain.services.meal_recommendation.catalog_recipe_seed_validator import (
    PRODUCTION_CUISINE_COUNTS,
    REQUIRED_CUISINES,
    validate_catalog_seed_manifest,
)

router = APIRouter(prefix="/v1/admin/meal-catalog", tags=["Admin Meal Catalog"])


@router.post(
    "/approve-food-reference",
    response_model=AdminMealCatalogApproveFoodReferenceResponse,
)
async def approve_admin_meal_catalog_food_reference(
    request: AdminMealCatalogApproveFoodReferenceRequest,
    db: AsyncSession = Depends(get_async_db),
    reviewer: CatalogFoodReferenceReviewService = Depends(
        get_catalog_food_reference_review_service
    ),
    _admin: str = Depends(require_admin_or_local),
) -> AdminMealCatalogApproveFoodReferenceResponse:
    """Record an admin's review before a food reference can publish catalog meals."""

    approval = await reviewer.approve(request.food_reference_id)
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Food reference not found",
        )
    await db.commit()
    return AdminMealCatalogApproveFoodReferenceResponse(**approval.__dict__)


@router.post("/resolve", response_model=AdminMealCatalogImportResponse)
async def resolve_admin_meal_catalog_ingredients(
    request: AdminMealCatalogImportRequest,
    db: AsyncSession = Depends(get_async_db),
    importer: CatalogMealSeedImporter = Depends(get_catalog_meal_seed_importer),
    _admin: str = Depends(require_admin_or_local),
) -> AdminMealCatalogImportResponse:
    """Validate and resolve a manifest without changing the catalog."""

    validation = _validate_manifest(request)
    if validation.errors:
        return _response(
            validation, _empty_summary(validation, dry_run=True), applied=False
        )
    summary = await _run_importer(importer, request, dry_run=True)
    return _response(validation, summary, applied=False)


@router.post("/enrich", response_model=AdminMealCatalogEnrichmentResponse)
async def enrich_admin_meal_catalog_candidates(
    request: AdminMealCatalogImportRequest,
    db: AsyncSession = Depends(get_async_db),
    importer: CatalogMealSeedImporter = Depends(get_catalog_meal_seed_importer),
    _admin: str = Depends(require_admin_or_local),
) -> AdminMealCatalogEnrichmentResponse:
    """Cache unverified FatSecret candidates without publishing catalog recipes."""

    validation = _validate_manifest(request)
    if validation.errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_validation_response(validation).model_dump(),
        )
    summary = await importer.enrich_missing_candidates(request.manifest)
    await db.commit()
    return AdminMealCatalogEnrichmentResponse(
        validation=_validation_response(validation),
        attempted=summary.attempted,
        enriched=summary.enriched,
        skipped_existing=summary.skipped_existing,
    )


@router.post("/import", response_model=AdminMealCatalogImportResponse)
async def import_admin_meal_catalog(
    request: AdminMealCatalogImportRequest,
    db: AsyncSession = Depends(get_async_db),
    importer: CatalogMealSeedImporter = Depends(get_catalog_meal_seed_importer),
    _admin: str = Depends(require_admin_or_local),
) -> AdminMealCatalogImportResponse:
    """Import a validated manifest, or preview it when ``dry_run`` is true."""

    validation = _validate_manifest(request)
    if validation.errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_response(
                validation,
                _empty_summary(validation, dry_run=request.dry_run),
                applied=False,
            ).model_dump(),
        )

    preview = await _run_importer(importer, request, dry_run=True)
    if not preview.is_successful or request.dry_run:
        return _response(validation, preview, applied=False)

    summary = await _run_importer(importer, request, dry_run=False)
    if not summary.is_successful:
        await db.rollback()
        return _response(validation, summary, applied=False)
    await db.commit()
    get_catalog_meal_snapshot_service().invalidate()
    return _response(validation, summary, applied=True)


def _validate_manifest(request: AdminMealCatalogImportRequest):
    recipes = request.manifest.get("recipes", [])
    if not isinstance(recipes, list):
        recipes = []
    expected_count = len(recipes) if request.partial else request.expected_recipe_count
    return validate_catalog_seed_manifest(
        request.manifest,
        expected_recipe_count=expected_count,
        min_per_cuisine_meal_type=request.min_per_cuisine_meal_type,
        expected_cuisine_counts=(
            None
            if request.partial or request.skip_exact_cuisine_count
            else PRODUCTION_CUISINE_COUNTS
        ),
        allow_declared_expected_count_mismatch=request.partial,
        allowed_cuisines=None if request.partial else REQUIRED_CUISINES,
        required_cuisines=() if request.partial else REQUIRED_CUISINES,
    )


async def _run_importer(
    importer: CatalogMealSeedImporter,
    request: AdminMealCatalogImportRequest,
    *,
    dry_run: bool,
):
    auto_resolve_threshold = (
        0.0
        if request.resolve_all_best_effort and request.auto_resolve_threshold is None
        else request.auto_resolve_threshold
    )
    return await importer.with_options(
        dry_run=dry_run,
        approved_mappings=request.resolver_map,
        auto_resolve_threshold=auto_resolve_threshold,
        resolve_all_best_effort=request.resolve_all_best_effort,
    ).import_manifest(request.manifest)


def _empty_summary(validation, *, dry_run: bool):
    from src.app.services.catalog_meal_seed_import_service import (
        CatalogSeedImportSummary,
    )

    return CatalogSeedImportSummary(
        dry_run=dry_run,
        errors=tuple(validation.errors),
    )


def _response(validation, summary, *, applied: bool) -> AdminMealCatalogImportResponse:
    report = summary.resolution_report()
    validation_response = _validation_response(validation)
    return AdminMealCatalogImportResponse(
        validation=validation_response,
        manifest_digest=validation.manifest_digest,
        recipe_count=validation.recipe_count,
        coverage=validation.coverage,
        inserted=summary.inserted,
        updated=summary.updated,
        skipped_existing=summary.skipped_existing,
        dry_run=summary.dry_run,
        applied=applied,
        errors=list(summary.errors),
        issues=report["issues"],
        unverified_references=report["unverified_references"],
        review_required=report["review_required"],
    )


def _validation_response(validation) -> AdminMealCatalogValidationResponse:
    return AdminMealCatalogValidationResponse(
        manifest_digest=validation.manifest_digest,
        recipe_count=validation.recipe_count,
        errors=list(validation.errors),
        coverage=validation.coverage,
    )

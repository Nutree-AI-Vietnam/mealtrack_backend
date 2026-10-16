"""
Feature flags API endpoints for application-level feature control.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.base_dependencies import get_cache_service
from src.api.dependencies.auth import get_current_user_id, require_admin
from src.api.schemas.request.feature_flag_requests import (
    CreateFeatureFlagRequest,
    UpdateFeatureFlagRequest,
)
from src.api.schemas.response.feature_flag_responses import (
    FeatureFlagCreatedResponse,
    FeatureFlagsResponse,
    FeatureFlagUpdatedResponse,
    IndividualFeatureFlagResponse,
)
from src.domain.cache.cache_keys import CacheKeys
from src.domain.utils.timezone_utils import utc_now
from src.infra.cache.cache_service import CacheService
from src.infra.database.config_async import get_async_db
from src.infra.database.models.feature_flag import FeatureFlag
from src.infra.services.feature_flag_service import FeatureFlagService

router = APIRouter(prefix="/v1/feature-flags", tags=["Feature Flags"])

# Server-only flags (entitlement rollout kill switches) are never listed to clients.
_SERVER_ONLY_FLAG_PREFIX = "ent_"


def _client_visible(response: FeatureFlagsResponse) -> FeatureFlagsResponse:
    flags = {
        name: enabled
        for name, enabled in response.flags.items()
        if not name.startswith(_SERVER_ONLY_FLAG_PREFIX)
    }
    return response.model_copy(update={"flags": flags})


@router.get("/", response_model=FeatureFlagsResponse)
async def get_feature_flags(
    db: AsyncSession = Depends(get_async_db),
    cache_service: CacheService | None = Depends(get_cache_service),
    _user_id: str = Depends(get_current_user_id),
):
    """
    Get all feature flags from the database.

    Returns every client-visible flag; server-only `ent_*` flags are omitted.
    """
    cache_key, ttl = CacheKeys.feature_flags()
    if cache_service:
        cached = await cache_service.get_json(cache_key)
        if cached:
            return _client_visible(FeatureFlagsResponse(**cached))

    result = await db.execute(select(FeatureFlag))
    feature_flags = result.scalars().all()
    flags_dict = {flag.name: flag.enabled for flag in feature_flags}

    response = FeatureFlagsResponse(flags=flags_dict, updated_at=utc_now())

    if cache_service:
        await cache_service.set_json(cache_key, response.model_dump(), ttl)

    return _client_visible(response)


@router.get("/{feature_name}", response_model=IndividualFeatureFlagResponse)
async def get_individual_feature_flag(
    feature_name: str,
    db: AsyncSession = Depends(get_async_db),
    cache_service: CacheService | None = Depends(get_cache_service),
    _user_id: str = Depends(get_current_user_id),
):
    """
    Get a specific feature flag from the database.

    - **feature_name**: Name of the feature flag to retrieve

    Returns the status of a single feature flag. Server-only `ent_*` flags
    answer 404, the same as a flag that does not exist.
    """
    if feature_name.startswith(_SERVER_ONLY_FLAG_PREFIX):
        raise HTTPException(
            status_code=404, detail=f"Feature flag '{feature_name}' not found"
        )

    cache_key, ttl = CacheKeys.feature_flag(feature_name)
    if cache_service:
        cached = await cache_service.get_json(cache_key)
        if cached:
            return IndividualFeatureFlagResponse(**cached)

    result = await db.execute(
        select(FeatureFlag).where(FeatureFlag.name == feature_name)
    )
    feature_flag = result.scalar_one_or_none()

    if not feature_flag:
        raise HTTPException(
            status_code=404, detail=f"Feature flag '{feature_name}' not found"
        )

    response = IndividualFeatureFlagResponse(
        name=feature_flag.name,
        enabled=feature_flag.enabled,
        description=feature_flag.description,
        created_at=feature_flag.created_at,
        updated_at=feature_flag.updated_at,
    )

    if cache_service:
        await cache_service.set_json(cache_key, response.model_dump(), ttl)

    return response


@router.post("/", response_model=FeatureFlagCreatedResponse, status_code=201)
async def create_feature_flag(
    request: CreateFeatureFlagRequest,
    cache_service: CacheService | None = Depends(get_cache_service),
    _admin: str = Depends(require_admin),
):
    """
    Create a new feature flag.

    - **name**: Unique name for the feature flag
    - **enabled**: Initial enabled state (default: False)
    - **description**: Optional description of the feature flag

    Returns the created feature flag information.
    """
    svc = FeatureFlagService()
    new_flag = await svc.create(request.name, request.enabled, request.description)

    response = FeatureFlagCreatedResponse(
        name=new_flag.name,
        enabled=new_flag.enabled,
        description=new_flag.description,
        created_at=new_flag.created_at,
    )

    if cache_service:
        await cache_service.invalidate(CacheKeys.feature_flags()[0])
        await cache_service.invalidate(CacheKeys.feature_flag(new_flag.name)[0])

    return response


@router.put("/{feature_name}", response_model=FeatureFlagUpdatedResponse)
async def update_feature_flag(
    feature_name: str,
    request: UpdateFeatureFlagRequest,
    cache_service: CacheService | None = Depends(get_cache_service),
    _admin: str = Depends(require_admin),
):
    """
    Update an existing feature flag.

    - **feature_name**: Name of the feature flag to update
    - **enabled**: New enabled state (optional)
    - **description**: New description (optional)

    Returns the updated feature flag information.
    """
    svc = FeatureFlagService()
    feature_flag = await svc.update(feature_name, request.enabled, request.description)

    response = FeatureFlagUpdatedResponse(
        name=feature_flag.name,
        enabled=feature_flag.enabled,
        description=feature_flag.description,
        updated_at=feature_flag.updated_at,
    )

    if cache_service:
        await cache_service.invalidate(CacheKeys.feature_flags()[0])
        await cache_service.invalidate(CacheKeys.feature_flag(feature_flag.name)[0])

    return response

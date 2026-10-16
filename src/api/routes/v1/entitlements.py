"""Entitlement endpoints (NM-680).

``GET /v1/me/entitlements`` and ``POST /v1/me/entitlements/sync`` answer the
schema-v1 contract. While ``ent_read_api`` is off (or ``ent_read_api_defer``
is on) they answer ``authority: "rc"`` / ``state: "unknown"`` and fetch
nothing, so the app keeps deciding from RevenueCat.

The admin endpoints write and inspect manual grants behind the hardened
``require_admin`` (D7).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from src.api.base_dependencies import (
    get_create_entitlement_grant_handler,
    get_entitlement_account_service,
    get_entitlement_query_service,
    get_revoke_entitlement_grant_handler,
)
from src.api.dependencies.auth import get_current_user_id, require_admin
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.entitlement_requests import (
    CreateEntitlementGrantRequest,
    EntitlementSyncRequest,
    RevokeEntitlementGrantRequest,
)
from src.api.schemas.response.entitlement_responses import (
    BillingProviderEventResponse,
    EntitlementDebugResponse,
    EntitlementGrantMutationResponse,
    EntitlementGrantResponse,
    EntitlementProjectionResponse,
    EntitlementsResponse,
    StoreSubscriptionResponse,
)
from src.app.commands.entitlements import (
    CreateEntitlementGrantCommand,
    RevokeEntitlementGrantCommand,
)
from src.app.schemas.entitlement_schemas import (
    EntitlementGrantResult,
    EntitlementsView,
)
from src.app.services.entitlement_account_service import (
    CLIENT_HINT_KINDS,
    PURCHASE_HINT_WINDOW,
    ClientHint,
)
from src.domain.model.billing.ledger import (
    BillingProviderEvent,
    EntitlementGrant,
    StoreSubscription,
    UserEntitlement,
)
from src.domain.services.billing.access_policy import effective_has_access
from src.domain.utils.timezone_utils import utc_now

router = APIRouter(prefix="/v1/me/entitlements", tags=["Entitlements"])
admin_router = APIRouter(prefix="/v1/admin/entitlements", tags=["Admin Entitlements"])

CACHE_CONTROL = "private, no-cache"
NO_STORE = "no-store"
# Abuse ceiling. The contract limit (6/min per user) is soft: over it the
# endpoint answers from the projection without a RevenueCat fetch.
SYNC_HARD_RATE_LIMIT = "30/minute"


@router.get(
    "",
    response_model=EntitlementsResponse,
    responses={304: {"description": "Not modified (If-None-Match)"}},
)
async def get_my_entitlements(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    service=Depends(get_entitlement_account_service),
) -> Response:
    """Entitlements of the signed-in user, from the projection.

    Weak ETag ``W/"{user_id}:{version}:{next_change_epoch}"``; ``If-None-Match``
    answers 304 only while ``now < next_change_at``.
    """
    view = await service.get_entitlements(user_id)
    return entitlements_response(
        view, if_none_match=request.headers.get("if-none-match")
    )


@router.post("/sync", response_model=EntitlementsResponse)
@limiter.limit(SYNC_HARD_RATE_LIMIT)
async def sync_my_entitlements(
    request: Request,
    body: EntitlementSyncRequest | None = Body(default=None),
    user_id: str = Depends(get_current_user_id),
    service=Depends(get_entitlement_account_service),
) -> Response:
    """Re-sync from the provider and answer the same body as ``GET``.

    With a ``client_hint`` (after purchase, restore or redeem) the provider is
    re-fetched and re-syncs follow at +5 s and +30 s; a negative answer is
    never cacheable. Hints past their own per-user budget lose that priority
    but are still recorded. Without one, the provider is re-fetched only when
    the last sync is older than 15 min. If the provider is unavailable the
    last known state is returned with ``stale: true`` and ``sync_error``.
    """
    del request  # required by the rate limiter
    view = await service.sync_entitlements(user_id, _client_hint(body))
    return entitlements_response(view, if_none_match=None)


def _client_hint(body: EntitlementSyncRequest | None) -> ClientHint | None:
    hint = body.client_hint if body is not None else None
    if hint is None or hint.kind not in CLIENT_HINT_KINDS:
        return None
    return ClientHint(
        kind=hint.kind,
        product_id=hint.product_id,
        transaction_id=hint.transaction_id,
    )


def entitlements_response(
    view: EntitlementsView,
    *,
    if_none_match: str | None,
    now: datetime | None = None,
) -> Response:
    """Contract body with the caching rules of plan §7.1."""
    now = now or view.server_time
    if view.stale or view.is_unconfirmed_negative(now, PURCHASE_HINT_WINDOW):
        # Neither a provider-error answer nor a "no" right after a purchase
        # may be revalidated later with a 304.
        return JSONResponse(
            content=view.to_contract(), headers={"Cache-Control": NO_STORE}
        )
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": view.etag}
    if (
        if_none_match
        and etag_matches(if_none_match, view.etag)
        and view.is_cacheable_at(now)
    ):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return JSONResponse(content=view.to_contract(), headers=headers)


def etag_matches(if_none_match: str, etag: str) -> bool:
    """Weak comparison (RFC 9110 §13.1.2) against a comma-separated list."""
    wanted = _opaque(etag)
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or (candidate and _opaque(candidate) == wanted):
            return True
    return False


def _opaque(tag: str) -> str:
    return tag[2:] if tag.startswith("W/") else tag


# ---- admin ----


@admin_router.post(
    "/grants",
    response_model=EntitlementGrantMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_entitlement_grant(
    body: CreateEntitlementGrantRequest,
    response: Response,
    admin_email: str = Depends(require_admin),
    handler=Depends(get_create_entitlement_grant_handler),
) -> EntitlementGrantMutationResponse:
    """Grant an entitlement from our ledger (max 366 days, reason required)."""
    result = await handler.handle(
        CreateEntitlementGrantCommand(
            user_id=body.user_id,
            source=body.source,
            reason=body.reason,
            ends_at=body.ends_at,
            created_by=admin_email,
            entitlement_key=body.entitlement_key,
            starts_at=body.starts_at,
            source_ref=body.source_ref,
        )
    )
    if not result.changed:
        response.status_code = status.HTTP_200_OK
    return _grant_mutation(result)


@admin_router.post(
    "/grants/{grant_id}/revoke", response_model=EntitlementGrantMutationResponse
)
async def revoke_entitlement_grant(
    grant_id: str,
    body: RevokeEntitlementGrantRequest,
    admin_email: str = Depends(require_admin),
    handler=Depends(get_revoke_entitlement_grant_handler),
) -> EntitlementGrantMutationResponse:
    """Revoke a manual grant; grants are never deleted."""
    result = await handler.handle(
        RevokeEntitlementGrantCommand(
            grant_id=grant_id, reason=body.reason, revoked_by=admin_email
        )
    )
    return _grant_mutation(result)


@admin_router.get("/{user_id}", response_model=EntitlementDebugResponse)
async def get_entitlement_debug_view(
    user_id: str,
    _admin: str = Depends(require_admin),
    query_service=Depends(get_entitlement_query_service),
) -> EntitlementDebugResponse:
    """Projection, store subscriptions, grants and the last 20 inbox rows."""
    debug = await query_service.get_debug_view(user_id)
    now = utc_now()
    return EntitlementDebugResponse(
        user_id=user_id,
        server_time=now,
        entitlements=EntitlementsView.from_projections(
            user_id, debug.projections, now
        ).to_contract(),
        projections=[_projection(row, now) for row in debug.projections],
        store_subscriptions=[
            _store_subscription(row) for row in debug.store_subscriptions
        ],
        grants=[_grant(row) for row in debug.grants],
        events=[_event(row) for row in debug.events],
    )


def _grant_mutation(result: EntitlementGrantResult) -> EntitlementGrantMutationResponse:
    return EntitlementGrantMutationResponse(
        grant=_grant(result.grant),
        changed=result.changed,
        projection=(
            _projection(result.projection, utc_now())
            if result.projection is not None
            else None
        ),
    )


def _grant(grant: EntitlementGrant) -> EntitlementGrantResponse:
    return EntitlementGrantResponse(
        id=grant.id,
        user_id=grant.user_id,
        entitlement_key=grant.entitlement_key,
        source=grant.source,
        source_ref=grant.source_ref,
        reason=grant.reason,
        starts_at=grant.starts_at,
        ends_at=grant.ends_at,
        revoked_at=grant.revoked_at,
        revoke_reason=grant.revoke_reason,
        revoked_by=grant.revoked_by,
        created_by=grant.created_by,
        created_at=grant.created_at,
    )


def _projection(row: UserEntitlement, now: datetime) -> EntitlementProjectionResponse:
    return EntitlementProjectionResponse(
        user_id=row.user_id,
        entitlement_key=row.entitlement_key,
        has_access=row.has_access,
        effective_has_access=effective_has_access(row, now),
        state=row.state,
        access_until=row.access_until,
        is_lifetime=row.is_lifetime,
        next_change_at=row.next_change_at,
        will_renew=row.will_renew,
        period_type=row.period_type,
        primary_source=row.primary_source,
        primary_ref=row.primary_ref,
        product_id=row.product_id,
        store=row.store,
        is_sandbox=row.is_sandbox,
        ever_had_access=row.ever_had_access,
        version=row.version,
        computed_at=row.computed_at,
        last_provider_sync_at=row.last_provider_sync_at,
        last_purchase_hint_at=row.last_purchase_hint_at,
    )


def _store_subscription(row: StoreSubscription) -> StoreSubscriptionResponse:
    return StoreSubscriptionResponse(
        id=row.id,
        provider=row.provider,
        store=row.store,
        environment=row.environment,
        subscription_key=row.subscription_key,
        source_customer_id=row.source_customer_id,
        product_id=row.product_id,
        entitlement_key=row.entitlement_key,
        state=row.state,
        period_type=row.period_type,
        ownership_type=row.ownership_type,
        will_renew=row.will_renew,
        is_lifetime=row.is_lifetime,
        purchased_at=row.purchased_at,
        expires_at=row.expires_at,
        grace_expires_at=row.grace_expires_at,
        unsubscribe_detected_at=row.unsubscribe_detected_at,
        billing_issue_detected_at=row.billing_issue_detected_at,
        refunded_at=row.refunded_at,
        absent_since=row.absent_since,
        fetched_at=row.fetched_at,
        last_synced_at=row.last_synced_at,
        version=row.version,
    )


def _event(row: BillingProviderEvent) -> BillingProviderEventResponse:
    return BillingProviderEventResponse(
        id=row.id,
        provider=row.provider,
        provider_event_id=row.provider_event_id,
        event_type=row.event_type,
        status=row.status,
        environment=row.environment,
        app_user_id=row.app_user_id,
        event_ts=row.event_ts,
        park_reason=row.park_reason,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        last_error=row.last_error,
        received_at=row.received_at,
        processed_at=row.processed_at,
    )

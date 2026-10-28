"""Subscription access support API (admin only, behind ``require_admin``).

Support finds a user, sees their rows and the access decision, can read
RevenueCat live without writing, resyncs a user from RevenueCat the way the
app's own refresh does, and works through the repair queue. Procedure:
docs/runbooks/subscription-access-reconciliation.md, "Support procedure".

Every call is logged at INFO with the admin's email, the action, the target
user id and the result. A resync is also counted
(``subscription_access.support_action``), without the email. Customer
emails and lookup queries are never logged. The lookup takes its query in a
POST body, never the URL, which the server's access log and Sentry's
request data record; Sentry events of that route have the body and stack
frame variables removed (``_PRIVATE_REQUEST_PATHS`` in
src/infra/monitoring/sentry.py).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from src.api.dependencies.auth import require_admin
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.premium_check import forget_revenuecat_check
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.admin_subscription_access_requests import (
    SupportUserLookupRequest,
)
from src.api.schemas.response.admin_subscription_access_responses import (
    SupportAccessDecisionResponse,
    SupportAccessFlagsResponse,
    SupportLiveEntitlementResponse,
    SupportLiveReadResponse,
    SupportResyncResponse,
    SupportSubscriptionRowResponse,
    SupportSyncResultResponse,
    SupportUserAccessResponse,
    SupportUserResponse,
    UnresolvedMarkerResponse,
    UnresolvedQueueItemResponse,
    UnresolvedQueueResponse,
)
from src.app.commands.subscription_access import ResyncSubscriptionAccessCommand
from src.app.queries.subscription_access import (
    GetSupportSubscriptionAccessQuery,
    ListUnresolvedSubscriptionAccessQuery,
)
from src.app.services.subscription_access_support import (
    AccessDecision,
    InvalidQueueCursor,
    LiveRead,
    SupportAccessView,
    SupportResyncOutcome,
    SupportUserAmbiguous,
    SupportUserNotFound,
    SupportUserNotLive,
    UnresolvedQueuePage,
    decode_queue_cursor,
    encode_queue_cursor,
)
from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessApplyError,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable
from src.observability import increment_metric

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/admin/subscription-access", tags=["Admin Subscription Access"]
)

NO_STORE = "private, no-store"
# Per admin (the limiter keys on the bearer token's uid). A resync is one
# RevenueCat read and one write transaction; a live lookup is one read.
# Shared limits: one bucket per admin whatever the path says, so resyncing
# many different users does not give each of them its own budget.
RESYNC_RATE_LIMIT = "10/minute"
RESYNC_RATE_LIMIT_SCOPE = "admin_subscription_access_resync"
LOOKUP_RATE_LIMIT = "30/minute"
LOOKUP_RATE_LIMIT_SCOPE = "admin_subscription_access_lookup"
DEFAULT_QUEUE_LIMIT = 50
MAX_QUEUE_LIMIT = 200

ACTION_LOOKUP = "lookup"
ACTION_RESYNC = "resync"
ACTION_QUEUE = "unresolved_queue"


def _error(
    status_code: int,
    error_code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message, "details": details or {}},
        headers={"Cache-Control": NO_STORE, **(headers or {})},
    )


def _audit(
    admin_email: str,
    action: str,
    *,
    target_user_id: str | None,
    result: str,
    **fields: Any,
) -> None:
    """The audit line every support call writes (INFO, never a customer email)."""
    extra = "".join(f" {key}={value}" for key, value in fields.items())
    logger.info(
        "Subscription access support call: admin=%s action=%s target_user_id=%s "
        "result=%s%s",
        admin_email,
        action,
        target_user_id or "-",
        result,
        extra,
    )


def _record_resync(admin_email: str, user_id: str, result: str, **fields: Any) -> None:
    _audit(admin_email, ACTION_RESYNC, target_user_id=user_id, result=result, **fields)
    # The admin's email stays out of metric attributes.
    increment_metric(
        "subscription_access.support_action",
        attributes={"operation": ACTION_RESYNC, "result": result, "user_id": user_id},
    )


# --- POST /users/lookup --------------------------------------------------------


@router.post("/users/lookup", response_model=SupportUserAccessResponse)
@limiter.shared_limit(LOOKUP_RATE_LIMIT, scope=LOOKUP_RATE_LIMIT_SCOPE)
async def lookup_user_access(
    request: Request,
    response: Response,
    body: SupportUserLookupRequest,
    admin_email: str = Depends(require_admin),
    event_bus=Depends(get_configured_event_bus),
) -> SupportUserAccessResponse:
    """One user's subscription rows and the access decision taken from them.

    The body's ``query`` is an email, a Firebase uid or an internal user id;
    ``live`` also reads RevenueCat. Nothing is written. A POST so that the
    query, usually a customer's email, stays out of URLs (access logs,
    Sentry).
    """
    response.headers["Cache-Control"] = NO_STORE
    try:
        view: SupportAccessView = await event_bus.send(
            GetSupportSubscriptionAccessQuery(body.query, live=body.live)
        )
    except SupportUserNotFound as error:
        _audit(admin_email, ACTION_LOOKUP, target_user_id=None, result="not_found")
        raise _error(404, "USER_NOT_FOUND", "No user matches the query.") from error
    except SupportUserAmbiguous as error:
        _audit(
            admin_email,
            ACTION_LOOKUP,
            target_user_id=None,
            result="ambiguous",
            matches=len(error.user_ids),
        )
        raise _error(
            409,
            "AMBIGUOUS_QUERY",
            "Several users match the query; look one up by its id.",
            details={"user_ids": error.user_ids},
        ) from error
    except Exception:
        _audit(admin_email, ACTION_LOOKUP, target_user_id=None, result="error")
        raise
    _audit(
        admin_email,
        ACTION_LOOKUP,
        target_user_id=view.user.id,
        result=view.decision.outcome,
        live=view.live.status if view.live is not None else "-",
    )
    return SupportUserAccessResponse(
        user=SupportUserResponse(
            id=view.user.id,
            firebase_uid=view.user.firebase_uid,
            email=view.user.email,
            is_active=view.user.is_active,
            deleted_at=view.user.deleted_at,
        ),
        subscriptions=[
            SupportSubscriptionRowResponse(
                id=row.id,
                provider=row.provider,
                status=row.status,
                product_id=row.product_id,
                platform=row.platform,
                purchased_at=row.purchased_at,
                expires_at=row.expires_at,
                grace_period_expires_at=row.grace_period_expires_at,
                provider_snapshot_at=row.provider_snapshot_at,
                provider_sync_required_at=row.provider_sync_required_at,
                provider_refund_pending_at=row.provider_refund_pending_at,
                provider_transfer_source_at=row.provider_transfer_source_at,
                is_sandbox=row.is_sandbox,
            )
            for row in view.subscriptions
        ],
        decision=_decision(view.decision),
        flags=SupportAccessFlagsResponse(
            subscription_access_enabled=view.flags.subscription_access_enabled,
            subscription_enforcement_enabled=view.flags.subscription_enforcement_enabled,
            enforcement_active=view.flags.enforcement_active,
        ),
        live=_live(view.live) if view.live is not None else None,
    )


def _decision(decision: AccessDecision) -> SupportAccessDecisionResponse:
    return SupportAccessDecisionResponse(
        outcome=decision.outcome,
        has_access=decision.has_access,
        status=decision.status,
        expires_at=decision.expires_at,
        product_id=decision.product_id,
        will_renew=decision.will_renew,
        provider=decision.provider,
        unresolved_reasons=list(decision.unresolved_reasons),
    )


def _live(read: LiveRead) -> SupportLiveReadResponse:
    peek = read.peek
    if peek is None:
        return SupportLiveReadResponse(
            status=read.status,
            reason=read.reason,
            retry_after_seconds=read.retry_after_seconds,
        )
    standard = peek.standard
    return SupportLiveReadResponse(
        status=read.status,
        read_at=peek.read_at,
        absent=peek.absent,
        entitlements=list(peek.entitlements),
        standard_missing=peek.standard_missing,
        standard=(
            SupportLiveEntitlementResponse(
                has_access=standard.has_access,
                status=standard.status,
                product_id=standard.product_id,
                platform=standard.platform,
                purchased_at=standard.purchased_at,
                expires_at=standard.expires_at,
                grace_period_expires_at=standard.grace_period_expires_at,
                period_type=standard.period_type,
                is_sandbox=standard.is_sandbox,
                will_renew=standard.will_renew,
            )
            if standard is not None
            else None
        ),
    )


# --- POST /users/{user_id}/resync ---------------------------------------------


@router.post("/users/{user_id}/resync", response_model=SupportResyncResponse)
@limiter.shared_limit(RESYNC_RATE_LIMIT, scope=RESYNC_RATE_LIMIT_SCOPE)
async def resync_user_access(
    request: Request,
    response: Response,
    user_id: str = Path(..., min_length=1, max_length=36),
    admin_email: str = Depends(require_admin),
    event_bus=Depends(get_configured_event_bus),
) -> SupportResyncResponse | JSONResponse:
    """Re-read the user's RevenueCat customer and apply it, like the app's
    POST /v1/me/entitlements/refresh does for its own user."""
    response.headers["Cache-Control"] = NO_STORE
    try:
        outcome: SupportResyncOutcome = await event_bus.send(
            ResyncSubscriptionAccessCommand(user_id)
        )
    except SupportUserNotFound as error:
        _record_resync(admin_email, user_id, "user_not_found")
        raise _error(404, "USER_NOT_FOUND", "No user has this id.") from error
    except SupportUserNotLive as error:
        _record_resync(admin_email, user_id, error.reason)
        raise _error(
            409,
            "USER_INACTIVE",
            "The account is deleted, deactivated or has no Firebase uid; "
            "RevenueCat is not read for it.",
            details={"reason": error.reason},
        ) from error
    except RevenueCatSnapshotUnavailable as error:
        # Logged, counted and (a contract failure) sent to Sentry where the
        # read failed. Nothing was written.
        reason = getattr(error, "reason", None) or "unknown"
        retry_after = getattr(error, "retry_after_seconds", None)
        _record_resync(admin_email, user_id, "revenuecat_unavailable", reason=reason)
        raise _error(
            503,
            "REVENUECAT_UNAVAILABLE",
            "RevenueCat could not be read; nothing was written. Try again later.",
            details={"reason": reason, "retry_after_seconds": retry_after},
            headers=(
                {"Retry-After": str(max(1, math.ceil(retry_after)))}
                if retry_after is not None
                else None
            ),
        ) from error
    except SubscriptionAccessApplyError:
        # Sent to Sentry where the apply failed (subscription_access.
        # sync_apply_error). Answered, not raised, so the framework does
        # not report it again.
        _record_resync(admin_email, user_id, "apply_failed")
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "error_code": "RESYNC_FAILED",
                    "message": "RevenueCat was read but its customer could not "
                    "be applied; nothing was written.",
                    "details": {},
                }
            },
            headers={"Cache-Control": NO_STORE},
        )
    except Exception:
        _record_resync(admin_email, user_id, "error")
        raise
    # RevenueCat was just read: a paid route the database still refuses may
    # ask it again instead of waiting out the last answer.
    await forget_revenuecat_check(outcome.user_id)
    sync = outcome.sync
    _record_resync(
        admin_email,
        user_id,
        sync.result,
        reason=sync.reason or "-",
        before=outcome.before.outcome,
        after=outcome.after.outcome,
    )
    return SupportResyncResponse(
        user_id=outcome.user_id,
        sync=SupportSyncResultResponse(
            result=sync.result,
            reason=sync.reason,
            rows_revoked=sync.rows_revoked,
            row_created=sync.row_created,
            markers_cleared=sync.markers_cleared,
            standard_missing=sync.standard_missing,
        ),
        before=_decision(outcome.before),
        after=_decision(outcome.after),
    )


# --- GET /unresolved -----------------------------------------------------------


@router.get("/unresolved", response_model=UnresolvedQueueResponse)
async def list_unresolved_access(
    response: Response,
    limit: int = Query(DEFAULT_QUEUE_LIMIT, ge=1, le=MAX_QUEUE_LIMIT),
    cursor: str | None = Query(
        None, max_length=512, description="next_cursor of the previous page"
    ),
    admin_email: str = Depends(require_admin),
    event_bus=Depends(get_configured_event_bus),
) -> UnresolvedQueueResponse:
    """The repair queue: live users whose RevenueCat rows carry a marker
    (sync required, refund pending, transfer source), oldest marker first."""
    response.headers["Cache-Control"] = NO_STORE
    try:
        after = decode_queue_cursor(cursor) if cursor else None
    except InvalidQueueCursor as error:
        _audit(admin_email, ACTION_QUEUE, target_user_id=None, result="bad_cursor")
        raise _error(400, "INVALID_CURSOR", "Unknown cursor.") from error
    try:
        page: UnresolvedQueuePage = await event_bus.send(
            ListUnresolvedSubscriptionAccessQuery(limit=limit, after=after)
        )
    except Exception:
        _audit(admin_email, ACTION_QUEUE, target_user_id=None, result="error")
        raise
    _audit(
        admin_email,
        ACTION_QUEUE,
        target_user_id=None,
        result="ok",
        items=len(page.items),
        more=page.next_cursor is not None,
    )
    return UnresolvedQueueResponse(
        items=[
            UnresolvedQueueItemResponse(
                user_id=user.user_id,
                firebase_uid=user.firebase_uid,
                oldest_marker_at=user.oldest_marker_at,
                oldest_marker_age_seconds=_age(page.checked_at, user.oldest_marker_at),
                markers=[
                    UnresolvedMarkerResponse(
                        type=kind,
                        marked_at=marked_at,
                        age_seconds=_age(page.checked_at, marked_at),
                    )
                    for kind, marked_at in user.markers()
                ],
            )
            for user in page.items
        ],
        next_cursor=(
            encode_queue_cursor(page.next_cursor)
            if page.next_cursor is not None
            else None
        ),
        limit=limit,
        checked_at=page.checked_at,
    )


def _age(now: datetime, then: datetime) -> int:
    """Whole seconds from ``then`` to ``now``; never negative."""
    return max(0, int((now - then).total_seconds()))

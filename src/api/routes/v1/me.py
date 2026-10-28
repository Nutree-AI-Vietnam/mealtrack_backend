"""Authenticated current-user endpoints.

GET /v1/me/entitlements answers from the database access policy. Its 503
error codes tell the mobile app what to do:

- ENTITLEMENTS_DISABLED: SUBSCRIPTION_ACCESS_ENABLED is off (the mobile kill
  switch). The app uses its RevenueCat client decision.
- ENTITLEMENTS_UNAVAILABLE: the database cannot decide safely right now
  (an unreconciled row and no other grant, a database failure, or an
  unexpected error). Not a denial; the app retries or falls back.

POST /v1/me/entitlements/refresh first re-reads the caller's RevenueCat
customer into the database (the self-heal for a missed webhook), then
answers exactly like the GET.
"""

import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.premium_check import forget_revenuecat_check
from src.api.middleware.rate_limit import limiter
from src.api.schemas.response.subscription_access import MyEntitlementsResponse
from src.app.commands.subscription_access import RefreshMyEntitlementsCommand
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.app.services.subscription_access_flags import (
    database_subscription_access_enabled,
)
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
)
from src.domain.ports.subscription_access_refresh_port import (
    SubscriptionAccessApplyError,
)
from src.domain.ports.subscription_service_port import RevenueCatSnapshotUnavailable
from src.observability import capture_exception, increment_metric

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/me", tags=["Me"])

ENTITLEMENTS_DISABLED = "ENTITLEMENTS_DISABLED"
ENTITLEMENTS_UNAVAILABLE = "ENTITLEMENTS_UNAVAILABLE"
NO_STORE = "private, no-store"
# Per Firebase user (the limiter keys on the bearer token's uid): about one
# RevenueCat read every ten seconds.
REFRESH_RATE_LIMIT = "6/minute"

_ERROR_MESSAGES = {
    ENTITLEMENTS_DISABLED: "Database subscription access is turned off.",
    ENTITLEMENTS_UNAVAILABLE: "Subscription access cannot be decided right now.",
}


def entitlements_error(
    error_code: str, *, retry_after_seconds: float | None = None
) -> HTTPException:
    """A 503 the client must not treat as a paywall denial."""
    headers = {"Cache-Control": NO_STORE}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(max(1, math.ceil(retry_after_seconds)))
    return HTTPException(
        status_code=503,
        detail={"error_code": error_code, "message": _ERROR_MESSAGES[error_code]},
        headers=headers,
    )


def _report_unexpected(
    error: Exception, *, user_id: str, operation: str, reported: bool = False
) -> None:
    """Log and report an error that would otherwise hide inside a 503.

    ``reported``: the error was already sent to Sentry where it happened
    (a snapshot apply failure, under its fixed fingerprint). It is counted
    and logged below ERROR, which Sentry's logging integration would turn
    into a second issue.
    """
    increment_metric(
        "subscription_access.entitlements_unavailable",
        attributes={"operation": operation, "failure_kind": "unexpected"},
    )
    if reported:
        logger.warning(
            "Entitlements could not be decided: operation=%s user_id=%s "
            "error_type=%s (reported where it happened)",
            operation,
            user_id,
            type(error).__name__,
        )
        return
    capture_exception(
        error,
        context={
            "component": "subscription_access",
            "operation": operation,
            "user_id": user_id,
            "error_type": type(error).__name__,
        },
    )
    logger.exception(
        "Entitlements could not be decided: operation=%s user_id=%s error_type=%s",
        operation,
        user_id,
        type(error).__name__,
    )


async def answer_entitlements(
    event_bus: Any, user_id: str, *, operation: str
) -> MyEntitlementsResponse:
    """The database decision for the user, or the 503 that replaces it."""
    if not database_subscription_access_enabled():
        raise entitlements_error(ENTITLEMENTS_DISABLED)
    try:
        result = await event_bus.send(GetMyEntitlementsQuery(user_id))
    except SubscriptionAccessUnavailable as error:
        increment_metric(
            "subscription_access.entitlements_unavailable",
            attributes={"operation": operation, "failure_kind": "unresolved"},
        )
        logger.info(
            "Entitlements unresolved in the database: operation=%s user_id=%s",
            operation,
            user_id,
        )
        raise entitlements_error(ENTITLEMENTS_UNAVAILABLE) from error
    except Exception as error:
        _report_unexpected(error, user_id=user_id, operation=operation)
        raise entitlements_error(ENTITLEMENTS_UNAVAILABLE) from error
    return MyEntitlementsResponse(**result)


@router.get("/entitlements", response_model=MyEntitlementsResponse)
async def get_my_entitlements(
    response: Response,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
) -> MyEntitlementsResponse:
    """Return the database-owned access decision for the signed-in user."""
    response.headers["Cache-Control"] = NO_STORE
    return await answer_entitlements(event_bus, user_id, operation="read")


@router.post("/entitlements/refresh", response_model=MyEntitlementsResponse)
@limiter.limit(REFRESH_RATE_LIMIT)
async def refresh_my_entitlements(
    request: Request,
    response: Response,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
) -> MyEntitlementsResponse:
    """Re-read the caller's RevenueCat customer, then answer like the GET.

    The refresh runs even while SUBSCRIPTION_ACCESS_ENABLED is off (it only
    writes verified provider facts, which helps reconciliation); the answer
    is then 503 ENTITLEMENTS_DISABLED.
    """
    response.headers["Cache-Control"] = NO_STORE
    try:
        await event_bus.send(RefreshMyEntitlementsCommand(user_id))
        # RevenueCat was just read: a paid route the database still refuses
        # may ask it again instead of waiting out the last answer.
        await forget_revenuecat_check(user_id)
    except RevenueCatSnapshotUnavailable as error:
        # Already logged, counted and reported where the read failed.
        if not database_subscription_access_enabled():
            raise entitlements_error(ENTITLEMENTS_DISABLED) from error
        raise entitlements_error(
            ENTITLEMENTS_UNAVAILABLE,
            retry_after_seconds=getattr(error, "retry_after_seconds", None),
        ) from error
    except SubscriptionAccessApplyError as error:
        _report_unexpected(error, user_id=user_id, operation="refresh", reported=True)
        if not database_subscription_access_enabled():
            raise entitlements_error(ENTITLEMENTS_DISABLED) from error
        raise entitlements_error(ENTITLEMENTS_UNAVAILABLE) from error
    except Exception as error:
        _report_unexpected(error, user_id=user_id, operation="refresh")
        if not database_subscription_access_enabled():
            raise entitlements_error(ENTITLEMENTS_DISABLED) from error
        raise entitlements_error(ENTITLEMENTS_UNAVAILABLE) from error
    return await answer_entitlements(event_bus, user_id, operation="refresh")

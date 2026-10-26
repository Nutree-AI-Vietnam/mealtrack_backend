"""Authenticated current-user endpoints.

GET /v1/me/entitlements answers from the database access policy. Its 503
error codes tell the mobile app what to do:

- ENTITLEMENTS_DISABLED: SUBSCRIPTION_ACCESS_ENABLED is off (the mobile kill
  switch). The app uses its RevenueCat client decision.
- ENTITLEMENTS_UNAVAILABLE: the database cannot decide safely right now
  (an unreconciled row and no other grant, a database failure, or an
  unexpected error). Not a denial; the app retries or falls back.
"""

import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.schemas.response.subscription_access import MyEntitlementsResponse
from src.app.queries.subscription_access import GetMyEntitlementsQuery
from src.app.services.subscription_access_flags import (
    database_subscription_access_enabled,
)
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
)
from src.observability import capture_exception, increment_metric

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/me", tags=["Me"])

ENTITLEMENTS_DISABLED = "ENTITLEMENTS_DISABLED"
ENTITLEMENTS_UNAVAILABLE = "ENTITLEMENTS_UNAVAILABLE"
NO_STORE = "private, no-store"

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


def _report_unexpected(error: Exception, *, user_id: str, operation: str) -> None:
    """Log and report an error that would otherwise hide inside a 503."""
    increment_metric(
        "subscription_access.entitlements_unavailable",
        attributes={"operation": operation, "failure_kind": "unexpected"},
    )
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

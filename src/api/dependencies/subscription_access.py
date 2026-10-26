"""Server-side subscription enforcement for paid backend routes.

Off by default: SUBSCRIPTION_ENFORCEMENT_ENABLED (and SUBSCRIPTION_ACCESS_ENABLED,
which it requires) must both be on, so deploying this changes nothing.
The gated routes are listed in docs/api-endpoints.md.
"""

import logging

from fastapi import Depends, HTTPException, status

from src.api.dependencies.auth import get_current_user_id
from src.app.queries.subscription_access import CheckSubscriptionAccessQuery
from src.app.services.subscription_access_flags import (
    subscription_enforcement_enabled,
)
from src.observability import increment_metric

logger = logging.getLogger(__name__)

SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"


def _event_bus():
    """Resolved only while enforcement is on, so routes keep their own bus."""
    from src.api.dependencies.event_bus import get_configured_event_bus

    return get_configured_event_bus()


async def require_active_subscription(
    user_id: str = Depends(get_current_user_id),
) -> None:
    """402 SUBSCRIPTION_REQUIRED unless the caller has subscription access."""
    if not subscription_enforcement_enabled():
        return
    if await _event_bus().send(CheckSubscriptionAccessQuery(user_id)):
        return
    increment_metric("subscription_access.enforcement_denied")
    logger.info("Paid route refused without subscription access: user_id=%s", user_id)
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error_code": SUBSCRIPTION_REQUIRED,
            "message": "Standard subscription required",
        },
    )

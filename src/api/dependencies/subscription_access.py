"""Server-side subscription enforcement for paid backend routes.

Off by default: SUBSCRIPTION_ENFORCEMENT_ENABLED (and SUBSCRIPTION_ACCESS_ENABLED,
which it requires) must both be on, so deploying this changes nothing.
The gated routes are listed in docs/api-endpoints.md.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status

from src.api.dependencies.auth import get_current_user_id
from src.app.queries.subscription_access import CheckSubscriptionAccessQuery
from src.app.services.subscription_access_flags import (
    subscription_enforcement_enabled,
)
from src.domain.ports.subscription_service_port import EntitlementCheck
from src.observability import increment_metric

if TYPE_CHECKING:
    from src.api.middleware.premium_check import RevenueCatAccess

logger = logging.getLogger(__name__)

SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"


def _event_bus():
    """Resolved only while enforcement is on, so routes keep their own bus."""
    from src.api.dependencies.event_bus import get_configured_event_bus

    return get_configured_event_bus()


async def _revenuecat_access(user_id: str) -> "RevenueCatAccess":
    """RevenueCat's short, cached check, shared with the Premium check."""
    from src.api.middleware.premium_check import revenuecat_access

    return await revenuecat_access(user_id)


async def require_active_subscription(
    user_id: str = Depends(get_current_user_id),
) -> None:
    """402 SUBSCRIPTION_REQUIRED unless the caller has subscription access.

    The database decides first. Before a 402, RevenueCat is asked (3 s in
    total, the Premium check's 60 s access cache): a payer whose purchase has no row
    yet (a webhook not yet applied or lost, a promotional grant) keeps the
    route. When RevenueCat cannot answer, the database's denial stands.
    This runs before the route's rate limiter, so RevenueCat is asked at most
    once per user per minute (premium_check.revenuecat_access).
    """
    if not subscription_enforcement_enabled():
        return
    if await _event_bus().send(CheckSubscriptionAccessQuery(user_id)):
        return
    revenuecat = await _revenuecat_access(user_id)
    if revenuecat.check is EntitlementCheck.ENTITLED:
        if revenuecat.from_revenuecat:
            increment_metric("subscription_access.enforcement_revenuecat_grant")
            logger.warning(
                "Paid route allowed by RevenueCat; the database grants no access: "
                "user_id=%s",
                user_id,
            )
        else:
            # A grant cached earlier, possibly from require_subscription's
            # own row check: not a payer RevenueCat just found.
            increment_metric(
                "subscription_access.enforcement_cached_grant",
                attributes={"source": revenuecat.source},
            )
            logger.info(
                "Paid route allowed by a cached grant; the database grants no "
                "access: user_id=%s source=%s",
                user_id,
                revenuecat.source,
            )
        return
    increment_metric(
        "subscription_access.enforcement_denied",
        attributes={"revenuecat": revenuecat.check.value, "source": revenuecat.source},
    )
    logger.info(
        "Paid route refused without subscription access: user_id=%s revenuecat=%s "
        "source=%s",
        user_id,
        revenuecat.check.value,
        revenuecat.source,
    )
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error_code": SUBSCRIPTION_REQUIRED,
            "message": "Standard subscription required",
        },
    )

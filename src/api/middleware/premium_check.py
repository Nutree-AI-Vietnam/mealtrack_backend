"""
Non-blocking subscription status for request handlers.

Paid routes are enforced by
src.api.dependencies.subscription_access.require_active_subscription.
"""

import logging
import os

from fastapi import Request

from src.api.base_dependencies import get_subscription_service
from src.domain.ports.subscription_service_port import SubscriptionServicePort

logger = logging.getLogger(__name__)


def _get_subscription_service() -> SubscriptionServicePort:
    """Helper to get subscription service - can be overridden in tests."""
    return get_subscription_service()


async def get_subscription_status(request: Request) -> dict:
    """
    Non-blocking subscription check that returns status info.

    Usage:
        @router.get("/feature")
        async def feature(status_info: dict = Depends(get_subscription_status)):
            if status_info["has_subscription"]:
                return {"data": "subscription"}
            else:
                return {"data": "basic"}
    """
    user = getattr(request.state, "user", None)

    if not user:
        return {"has_subscription": False, "subscription": None, "source": "no_user"}

    # Check local cache
    subscription = user.get_active_subscription()

    if subscription:
        return {
            "has_subscription": True,
            "subscription": {
                "product_id": subscription.product_id,
                "expires_at": (
                    subscription.expires_at.isoformat()
                    if subscription.expires_at
                    else None
                ),
                "is_monthly": subscription.is_monthly(),
                "is_yearly": subscription.is_yearly(),
            },
            "source": "cache",
        }

    # Check RevenueCat if configured
    revenuecat_secret_key = os.getenv("REVENUECAT_SECRET_API_KEY", "")
    if revenuecat_secret_key:
        subscription_service = _get_subscription_service()
        sub_info = await subscription_service.get_subscription_info(user.id)

        if sub_info:
            return {
                "has_subscription": True,
                "subscription": sub_info,
                "source": "revenuecat_api",
            }

    return {"has_subscription": False, "subscription": None, "source": "none"}

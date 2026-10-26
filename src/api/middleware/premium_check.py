"""
Subscription access validation middleware.

The subscriptions table (kept by RevenueCat webhooks) answers first. RevenueCat
is asked only when no paid-up row exists, which covers a purchase whose webhook
has not arrived yet.
"""

import logging
import os

from fastapi import HTTPException, Request, status

from src.api.base_dependencies import (
    get_cache_service,
    get_read_only_uow_factory,
    get_subscription_service,
)
from src.api.exceptions import ExternalServiceException
from src.domain.ports.cache_port import CachePort
from src.domain.ports.subscription_service_port import (
    EntitlementCheck,
    SubscriptionServicePort,
)

logger = logging.getLogger(__name__)

SUBSCRIPTION_ACCESS_CACHE_TTL_SECONDS = 60
REVENUECAT_CHECK_TIMEOUT_SECONDS = 3.0


def _get_subscription_service() -> SubscriptionServicePort:
    """Helper to get subscription service - can be overridden in tests."""
    return get_subscription_service()


def _get_uow_factory():
    """Helper to get a read-only unit of work factory - can be overridden in tests."""
    return get_read_only_uow_factory()


def _get_cache() -> CachePort | None:
    """Helper to get the Redis cache - can be overridden in tests."""
    return get_cache_service()


def _dev_access_allowed() -> bool:
    """Only the explicit local auth bypass skips the check; unset ENVIRONMENT never does."""
    return (
        os.getenv("ENVIRONMENT") == "development"
        and os.getenv("ENABLE_DEV_AUTH_BYPASS") == "1"
    )


async def require_subscription(user_id: str) -> None:
    """
    Require an active standard subscription for the user.

    Call it inside the route body, after the rate limiter, with the id from
    ``get_current_user_id``. Raises 402 ``SUBSCRIPTION_REQUIRED``, or 503
    ``SUBSCRIPTION_CHECK_UNAVAILABLE`` when RevenueCat cannot answer, so an
    outage never sends a paying user to the paywall. Both answers are cached
    for 60 s per user.
    """
    if _dev_access_allowed():
        return

    cache = _get_cache()
    access = await _cached_access(cache, user_id)
    if access is None:
        access = await _check_access(user_id)
        if access is not EntitlementCheck.UNAVAILABLE:
            await _cache_access(cache, user_id, access)

    if access is EntitlementCheck.ENTITLED:
        return
    if access is EntitlementCheck.UNAVAILABLE:
        raise ExternalServiceException(
            "Subscription status is temporarily unavailable",
            "SUBSCRIPTION_CHECK_UNAVAILABLE",
        )
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "message": "Standard subscription required",
            "error_code": "SUBSCRIPTION_REQUIRED",
        },
    )


async def _check_access(user_id: str) -> EntitlementCheck:
    async with _get_uow_factory()() as uow:
        if await uow.subscriptions.has_entitled_subscription(
            user_id, exclude_sandbox=os.getenv("ENVIRONMENT") == "production"
        ):
            return EntitlementCheck.ENTITLED
        user = await uow.users.find_by_id(user_id)

    firebase_uid = getattr(user, "firebase_uid", None)
    if not firebase_uid:
        return EntitlementCheck.NOT_ENTITLED

    # No paid-up row - verify with RevenueCat (source of truth)
    if not os.getenv("REVENUECAT_SECRET_API_KEY", ""):
        logger.warning("REVENUECAT_SECRET_API_KEY not configured")
        return EntitlementCheck.NOT_ENTITLED

    access = await _get_subscription_service().check_entitlement(
        firebase_uid, timeout_seconds=REVENUECAT_CHECK_TIMEOUT_SECONDS
    )
    if access is EntitlementCheck.ENTITLED:
        # Webhook failed or is delayed - allow, the webhook will sync eventually
        logger.warning(
            "User %s has subscription in RevenueCat but not in cache", user_id
        )
    return access


def _cache_key(user_id: str) -> str:
    return f"subscription_access:{user_id}"


async def _cached_access(
    cache: CachePort | None, user_id: str
) -> EntitlementCheck | None:
    if cache is None:
        return None
    try:
        cached = await cache.get(_cache_key(user_id))
    except Exception:
        logger.warning("Subscription access cache read failed", exc_info=True)
        return None
    if cached in (EntitlementCheck.ENTITLED, EntitlementCheck.NOT_ENTITLED):
        return EntitlementCheck(cached)
    return None


async def _cache_access(
    cache: CachePort | None, user_id: str, access: EntitlementCheck
) -> None:
    if cache is None:
        return
    try:
        await cache.set(
            _cache_key(user_id), access.value, SUBSCRIPTION_ACCESS_CACHE_TTL_SECONDS
        )
    except Exception:
        logger.warning("Subscription access cache write failed", exc_info=True)


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

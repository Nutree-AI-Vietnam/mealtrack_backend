"""
Subscription access validation middleware.

``require_subscription`` is the always-on Premium check behind the Ghi nhanh
describe routes. The subscriptions table (kept by RevenueCat webhooks) answers
first. RevenueCat is asked only when no paid-up row exists, which covers a
purchase whose webhook has not arrived yet.

It is separate from
src.api.dependencies.subscription_access.require_active_subscription, which
gates the other paid routes behind SUBSCRIPTION_ENFORCEMENT_ENABLED (off by
default). ``get_subscription_status`` is a non-blocking status read.
"""

import asyncio
import functools
import logging
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

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
# Paid-route enforcement asks RevenueCat before a 402 at most once per user
# per this many seconds, whatever the answer. Its dependency runs before the
# route's rate limiter, so this is what bounds the RevenueCat reads a refused
# caller can cause.
REVENUECAT_RECHECK_SECONDS = 60
# The total bound on that check's RevenueCat call. httpx's timeout
# (REVENUECAT_CHECK_TIMEOUT_SECONDS) applies to each phase of the request on
# its own (connect, write, every read), so alone it allows several times 3 s.
REVENUECAT_CHECK_DEADLINE_SECONDS = 3.0
# Held under the recheck key while a worker is asking RevenueCat.
_CHECK_IN_FLIGHT = "in_flight"
# Bounds the per-process fallback kept while the shared cache is unavailable.
_LOCAL_CHECKS_MAX = 10_000

# Where revenuecat_access's answer came from.
ANSWER_ACCESS_CACHE = "access_cache"  # a grant either check cached (DB or RC)
ANSWER_RECENT_CHECK = "recent_check"  # held from a check within the window
ANSWER_REVENUECAT = "revenuecat"  # RevenueCat answered this request's call
ANSWER_NOT_ASKED = "not_asked"  # no Firebase uid, or RevenueCat not configured


@dataclass(frozen=True)
class RevenueCatAccess:
    """revenuecat_access's answer and where it came from (ANSWER_*).

    Only ANSWER_REVENUECAT is RevenueCat's answer to this request (a call it
    made or shared with concurrent ones). A grant from the access cache may
    be the database's: require_subscription caches its row grants there too.
    """

    check: EntitlementCheck
    source: str

    @property
    def from_revenuecat(self) -> bool:
        return self.source == ANSWER_REVENUECAT


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
    outage never sends a paying user to the paywall. Only access is cached
    (60 s per user); a 402 never is, so a purchase made at the paywall counts
    on the very next request.
    """
    if _dev_access_allowed():
        return

    cache = _get_cache()
    if await _has_cached_access(cache, user_id):
        return

    access = await _check_access(user_id)
    if access is EntitlementCheck.ENTITLED:
        await _cache_access(cache, user_id)
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


async def revenuecat_access(user_id: str) -> RevenueCatAccess:
    """RevenueCat's answer for a user the database does not grant.

    For paid-route enforcement, before a 402. A grant in the Premium check's
    60 s access cache answers without a call. Otherwise RevenueCat is asked
    (``check_entitlement``, cut off after REVENUECAT_CHECK_DEADLINE_SECONDS
    in total) at most once per user per REVENUECAT_RECHECK_SECONDS, whatever
    it answers: concurrent requests in one process share the call, and the
    answer is kept under its own key (``revenuecat_check_key``) in the shared
    cache, or in this process while that cache is unavailable. A request that finds another worker's call in
    flight gets UNAVAILABLE. A grant also goes to the access cache.
    UNAVAILABLE when RevenueCat cannot answer or is not configured. The
    answer says where it came from, so only RevenueCat's own grants are
    counted as such.
    """
    cache = _get_cache()
    if await _has_cached_access(cache, user_id):
        return RevenueCatAccess(EntitlementCheck.ENTITLED, ANSWER_ACCESS_CACHE)
    return await _single_flight(user_id, lambda: _throttled_check(cache, user_id))


async def forget_revenuecat_check(user_id: str) -> None:
    """Let the user's next refused paid request ask RevenueCat again.

    For writers that just read and applied the user's RevenueCat customer (a
    refresh, a synced webhook, a web-funnel finalize): a payer the database
    still denies is not held to an answer older than that read.
    """
    _local_checks.pop(user_id, None)
    cache = _get_cache()
    if cache is None:
        return
    try:
        await cache.invalidate(revenuecat_check_key(user_id))
    except Exception:
        logger.warning("RevenueCat recheck invalidation failed", exc_info=True)


def revenuecat_check_key(user_id: str) -> str:
    return f"subscription_revenuecat_check:{user_id}"


async def _throttled_check(cache: CachePort | None, user_id: str) -> RevenueCatAccess:
    recent = await _recent_check(cache, user_id)
    if recent is not None:
        return RevenueCatAccess(recent, ANSWER_RECENT_CHECK)
    async with _get_uow_factory()() as uow:
        user = await uow.users.find_by_id(user_id)
    firebase_uid = getattr(user, "firebase_uid", None)
    if not firebase_uid:
        return RevenueCatAccess(EntitlementCheck.NOT_ENTITLED, ANSWER_NOT_ASKED)
    if not os.getenv("REVENUECAT_SECRET_API_KEY", ""):
        logger.warning("REVENUECAT_SECRET_API_KEY not configured")
        return RevenueCatAccess(EntitlementCheck.UNAVAILABLE, ANSWER_NOT_ASKED)
    claim = await _claim_check(cache, user_id)
    if claim is None:
        # Another worker is asking RevenueCat for this user right now.
        return RevenueCatAccess(EntitlementCheck.UNAVAILABLE, ANSWER_RECENT_CHECK)
    access = await _ask_revenuecat(firebase_uid)
    await _remember_check(cache if claim else None, user_id, access)
    if access is EntitlementCheck.ENTITLED:
        await _cache_access(cache, user_id)
    return RevenueCatAccess(access, ANSWER_REVENUECAT)


async def _ask_revenuecat(firebase_uid: str) -> EntitlementCheck:
    """check_entitlement, UNAVAILABLE once REVENUECAT_CHECK_DEADLINE_SECONDS pass."""
    try:
        return await asyncio.wait_for(
            _get_subscription_service().check_entitlement(
                firebase_uid, timeout_seconds=REVENUECAT_CHECK_TIMEOUT_SECONDS
            ),
            REVENUECAT_CHECK_DEADLINE_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "RevenueCat entitlement check passed its %.1f s deadline",
            REVENUECAT_CHECK_DEADLINE_SECONDS,
        )
        return EntitlementCheck.UNAVAILABLE


# Checks in flight in this process, shared by concurrent requests per user.
_checks_in_flight: dict[str, asyncio.Task[RevenueCatAccess]] = {}


async def _single_flight(
    user_id: str, check: Callable[[], Coroutine[Any, Any, RevenueCatAccess]]
) -> RevenueCatAccess:
    loop = asyncio.get_running_loop()
    task = _checks_in_flight.get(user_id)
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(check())
        _checks_in_flight[user_id] = task
        task.add_done_callback(functools.partial(_check_finished, user_id))
    # A request that goes away (client disconnect) leaves the call running
    # for the others.
    return await asyncio.shield(task)


def _check_finished(user_id: str, task: asyncio.Task[RevenueCatAccess]) -> None:
    if _checks_in_flight.get(user_id) is task:
        del _checks_in_flight[user_id]
    if not task.cancelled():
        task.exception()  # retrieved, so a call nobody awaits any more stays quiet


# user_id -> (monotonic expiry, stored answer): the recheck key for this
# process, used only while the shared cache cannot hold it.
_local_checks: dict[str, tuple[float, str]] = {}


def _local_get(user_id: str) -> str | None:
    entry = _local_checks.get(user_id)
    if entry is None:
        return None
    if entry[0] <= time.monotonic():
        _local_checks.pop(user_id, None)
        return None
    return entry[1]


def _local_put(user_id: str, value: str) -> None:
    now = time.monotonic()
    if user_id not in _local_checks and len(_local_checks) >= _LOCAL_CHECKS_MAX:
        for key in [key for key, entry in _local_checks.items() if entry[0] <= now]:
            del _local_checks[key]
        if len(_local_checks) >= _LOCAL_CHECKS_MAX:
            _local_checks.clear()
    _local_checks[user_id] = (now + REVENUECAT_RECHECK_SECONDS, value)


def _stored_answer(value: object) -> EntitlementCheck | None:
    if value is None:
        return None
    if value == _CHECK_IN_FLIGHT:
        return EntitlementCheck.UNAVAILABLE
    try:
        return EntitlementCheck(value)
    except ValueError:
        return None


async def _recent_check(
    cache: CachePort | None, user_id: str
) -> EntitlementCheck | None:
    """The answer of the user's last check, if within the recheck window."""
    stored: object = _local_get(user_id)
    if stored is None and cache is not None:
        try:
            stored = await cache.get(revenuecat_check_key(user_id))
        except Exception:
            logger.warning("RevenueCat recheck read failed", exc_info=True)
    return _stored_answer(stored)


async def _claim_check(cache: CachePort | None, user_id: str) -> bool | None:
    """Claim the user's next RevenueCat call for the recheck window.

    True when claimed in the shared cache, False when claimed in this process
    (the shared cache could not answer), None when another worker holds it.
    """
    claimed: bool | None = None
    if cache is not None:
        try:
            claimed = await cache.add(
                revenuecat_check_key(user_id),
                _CHECK_IN_FLIGHT,
                REVENUECAT_RECHECK_SECONDS,
            )
        except Exception:
            logger.warning("RevenueCat recheck claim failed", exc_info=True)
    if claimed is None:
        _local_put(user_id, _CHECK_IN_FLIGHT)
        return False
    return True if claimed else None


async def _remember_check(
    cache: CachePort | None, user_id: str, access: EntitlementCheck
) -> None:
    if cache is not None:
        try:
            await cache.set(
                revenuecat_check_key(user_id),
                access.value,
                REVENUECAT_RECHECK_SECONDS,
            )
            return
        except Exception:
            logger.warning("RevenueCat recheck write failed", exc_info=True)
    _local_put(user_id, access.value)


def _cache_key(user_id: str) -> str:
    return f"subscription_access:{user_id}"


async def _has_cached_access(cache: CachePort | None, user_id: str) -> bool:
    if cache is None:
        return False
    try:
        cached = await cache.get(_cache_key(user_id))
    except Exception:
        logger.warning("Subscription access cache read failed", exc_info=True)
        return False
    # Any other value, such as a 402 cached before only access was, is a miss.
    return cached == EntitlementCheck.ENTITLED


async def _cache_access(cache: CachePort | None, user_id: str) -> None:
    if cache is None:
        return
    try:
        await cache.set(
            _cache_key(user_id),
            EntitlementCheck.ENTITLED.value,
            SUBSCRIPTION_ACCESS_CACHE_TTL_SECONDS,
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

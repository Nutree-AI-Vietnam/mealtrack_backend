"""RevenueCat webhook events for lifetime purchases and their refunds.

A lifetime purchase arrives as a NON_RENEWING_PURCHASE and grants
``standard`` with no expiry. The upgrade bought right after a yearly plan
converts that plan's row; a lifetime bought outright gets its own row. A
refund arrives as CANCELLATION (RevenueCat has no REFUND event type) and
marks only the row of that purchase refunded. Renewing-product events leave
lifetime rows alone; see ``webhook_subscription_rows``. A purchase made on
Play also returns the buyer's Play plans that still renew, for the router to
cancel after commit (``webhook_play_renewal_selection``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.api.base_dependencies import get_subscription_service
from src.api.exceptions import ExternalServiceException
from src.api.routes.v1.webhook_lifecycle_events import notify_affiliate
from src.api.routes.v1.webhook_lookup_parsing import parse_platform, parse_timestamp
from src.api.routes.v1.webhook_play_renewal_selection import (
    PlayRenewal,
    play_renewals_to_cancel,
)
from src.api.routes.v1.webhook_referral_funnel import (
    credit_referral_on_purchase,
    revoke_referral_on_refund,
)
from src.api.routes.v1.webhook_subscription_lifecycle import _drop_billing_issue_push
from src.api.routes.v1.webhook_subscription_rows import (
    apply_subscription_info,
    had_paid_purchase,
    has_paid_access,
    is_sandbox_event,
    load_subscriber_rows,
    lock_subscriber,
    new_subscription_row,
    users_row_first,
)
from src.domain.constants.lifetime_products import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    LIFETIME_UPGRADE_PRODUCT_ID,
    grants_access,
    is_lifetime_product,
    lifetime_base_id,
)
from src.domain.ports.subscription_service_port import EntitlementCheck
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)


def _get_subscription_service():
    """Resolve the RevenueCat service through the existing dependency boundary."""
    return get_subscription_service()


def _subscriber_id(event: dict[str, Any]) -> str | None:
    value = event.get("app_user_id")
    return value if isinstance(value, str) and value else None


async def handle_lifetime_purchase(
    uow: Any,
    user: Any,  # the event's User row; only its id is read
    event: dict[str, Any],
    affiliate_handler: Any = None,
    event_publisher: Any = None,
) -> list[PlayRenewal]:
    """Store a lifetime purchase on its own or the upgraded row, once per transaction.

    Returns the buyer's Play plans that still renew when the purchase was
    made on Play; none for a purchase stored before.
    """
    product_id = lifetime_base_id(event.get("product_id"))
    subscriber_id = _subscriber_id(event)
    transaction_id = event.get("transaction_id")
    if product_id is None or subscriber_id is None:
        # Rows are keyed by subscriber, so a retry could never store this one.
        # Premium still comes from RevenueCat's entitlement.
        logger.error("Lifetime purchase without app_user_id: user_id=%s", user.id)
        return []

    await lock_subscriber(uow, subscriber_id)
    rows = await load_subscriber_rows(uow, subscriber_id)
    if transaction_id and any(r.store_transaction_id == transaction_id for r in rows):
        # A redelivery must neither reactivate a refunded row nor publish twice.
        logger.info(
            "Lifetime purchase already stored: user_id=%s transaction=%s",
            user.id,
            transaction_id,
        )
        return []
    if not transaction_id:
        logger.warning("Lifetime purchase has no transaction id: user_id=%s", user.id)

    # Read before the upgrade turns the yearly row into lifetime.
    play_renewals = play_renewals_to_cancel(rows, user, event)
    reported_before = had_paid_purchase(rows)
    subscription = _purchase_row(
        rows, product_id, user, sandbox=is_sandbox_event(event)
    )
    if subscription is None:
        subscription = new_subscription_row(
            user, event, product_id=product_id, expires_at=None
        )
        uow.session.add(subscription)
    else:
        _make_lifetime(subscription, user, event, product_id)
    logger.info("User %s bought lifetime %s", user.id, product_id)

    await credit_referral_on_purchase(uow, str(user.id))
    if product_id == LIFETIME_UPGRADE_PRODUCT_ID or reported_before:
        # The buyer's first purchase was already reported as the initial one.
        logger.info(
            "Lifetime purchase not reported as initial: user_id=%s product=%s",
            user.id,
            product_id,
        )
    else:
        await notify_affiliate(
            affiliate_handler,
            "subscription_initial_purchase",
            {
                "mealtrack_user_id": str(user.id),
                "product_id": product_id,
                "period_type": event.get("period_type"),
                "subscription_id": transaction_id
                or event.get("original_transaction_id"),
                "occurred_at": (
                    parse_timestamp(event.get("purchased_at_ms")) or utc_now()
                ).isoformat(),
            },
            event.get("id"),
            event_publisher=event_publisher,
            user_id=str(user.id),
        )
    await _drop_billing_issue_push(uow, user)
    return play_renewals


def _purchase_row(
    rows: list[Any], product_id: str, user: Any, *, sandbox: bool
) -> Any | None:
    """This product's earlier row, else the running plan row the upgrade converts.

    A lifetime bought outright gets its own row beside any plan (a web-funnel
    plan is a separate billing). Only rows of the event's environment
    qualify: a sandbox purchase never converts a production row.
    """
    same_env = [row for row in rows if bool(row.is_sandbox) == sandbox]
    if len(same_env) < len(rows):
        logger.info("Lifetime purchase leaves the other environment's rows alone")
    earlier = next((row for row in same_env if row.product_id == product_id), None)
    if earlier is not None or product_id != LIFETIME_UPGRADE_PRODUCT_ID:
        return earlier
    now = utc_now()
    running = [row for row in same_env if _plan_still_running(row, now)]
    yearly = [row for row in running if _is_yearly(row.product_id)]
    return users_row_first(yearly or running, user)


def _is_yearly(product_id: str) -> bool:
    lowered = product_id.lower()
    return "yearly" in lowered or "annual" in lowered


def _plan_still_running(row: Any, now: datetime) -> bool:
    """A renewing plan whose paid period is still ahead.

    A cancelled plan counts: the upgrade asks the buyer to turn off renewal,
    and that arrives as CANCELLATION while the year is still paid.
    """
    return (
        not is_lifetime_product(row.product_id)
        and row.status not in ("refunded", "expired")
        and grants_access("active", row.product_id, row.expires_at, now)
    )


def _make_lifetime(row: Any, user: Any, event: dict[str, Any], product_id: str) -> None:
    if row.product_id == product_id:  # bought again after a refund
        row.purchased_at = parse_timestamp(event.get("purchased_at_ms")) or utc_now()
    row.user_id = user.id
    row.product_id = product_id
    row.platform = parse_platform(event.get("store"))
    row.status = "active"
    row.expires_at = None
    row.cancelled_at = None
    row.store_transaction_id = event.get("transaction_id")
    row.updated_at = utc_now()


@dataclass(frozen=True)
class RevenueCatPlan:
    """RevenueCat's answer on a subscriber, asked before any database lock."""

    current: dict[str, Any] | None  # the renewing plan it reports as active
    access: EntitlementCheck


async def fetch_revenuecat_plan(event: dict[str, Any]) -> RevenueCatPlan | None:
    """Ask RevenueCat about the event's subscriber; None without a subscriber.

    The router calls this before its unit of work opens, so no pooled
    connection or subscriber lock waits on RevenueCat. The adapter reports an
    HTTP failure as "no plan", so an empty answer is checked again with the
    entitlement check, which says UNAVAILABLE when RevenueCat cannot answer.
    """
    subscriber_id = _subscriber_id(event)
    if subscriber_id is None:
        return None
    service = _get_subscription_service()
    try:
        current = await service.get_subscription_info(subscriber_id)
        if current:
            return RevenueCatPlan(current, EntitlementCheck.ENTITLED)
        return RevenueCatPlan(None, await service.check_entitlement(subscriber_id))
    except Exception:
        logger.warning("RevenueCat lookup failed for a lifetime refund", exc_info=True)
        return RevenueCatPlan(None, EntitlementCheck.UNAVAILABLE)


async def handle_lifetime_refund(
    uow: Any,
    user: Any,
    event: dict[str, Any],
    affiliate_handler: Any = None,
    event_publisher: Any = None,
    plan: RevenueCatPlan | None = None,
) -> None:
    """Mark the refunded lifetime purchase's row refunded, and only that row.

    ``plan`` is RevenueCat's answer, fetched by the router before the unit of
    work; direct callers may leave it out and it is fetched here.
    """
    product_id = lifetime_base_id(event.get("product_id"))
    subscriber_id = _subscriber_id(event)
    if plan is None:
        plan = await fetch_revenuecat_plan(event)
    await lock_subscriber(uow, subscriber_id)
    rows = await load_subscriber_rows(uow, subscriber_id)
    target = _refunded_row(
        rows, product_id, event.get("transaction_id"), sandbox=is_sandbox_event(event)
    )
    other_access = has_paid_access(
        rows, sandbox=is_sandbox_event(event), exclude=target
    )
    if (
        product_id == LIFETIME_UPGRADE_PRODUCT_ID
        and not other_access
        and plan is not None
        and plan.access is EntitlementCheck.UNAVAILABLE
    ):
        # The yearly plan under the upgrade may still be paid. Nothing is
        # written or published: RevenueCat redelivers the refund later. ERROR,
        # so a refund whose every retry fails is seen instead of dropped (the
        # buyer would keep lifetime). The 503 handler logs nothing itself.
        logger.error(
            "Upgrade refund deferred, RevenueCat unavailable: event_id=%s "
            "event_type=%s environment=%s user_id=%s product=%s",
            event.get("id"),
            event.get("type"),
            event.get("environment"),
            user.id,
            product_id,
        )
        raise ExternalServiceException(
            "RevenueCat is unavailable; the refund will be redelivered",
            "REVENUECAT_UNAVAILABLE",
        )

    restored = None
    if target is None:
        logger.warning(
            "Lifetime refund matches no row: user_id=%s product=%s",
            user.id,
            product_id,
        )
    else:
        target.status = "refunded"
        target.updated_at = utc_now()
        logger.info("User %s lifetime %s refunded", user.id, product_id)
        if subscriber_id and not other_access and plan and plan.current:
            restored = _restore_renewing_row(
                uow, user, subscriber_id, event, rows, target, plan.current
            )

    await notify_affiliate(
        affiliate_handler,
        "subscription_refund",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": product_id,
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": utc_now().isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )
    # The referral stands while the buyer keeps a paid plan.
    revenuecat_entitled = plan is not None and plan.access is EntitlementCheck.ENTITLED
    if other_access or restored is not None or revenuecat_entitled:
        logger.info("Lifetime refund keeps the referral: user_id=%s", user.id)
    else:
        await revoke_referral_on_refund(uow, str(user.id))


def _refunded_row(
    rows: list[Any], product_id: str | None, transaction_id: object, *, sandbox: bool
) -> Any | None:
    """The purchase's row by transaction, else a row of that product and environment."""
    lifetime_rows = [row for row in rows if is_lifetime_product(row.product_id)]
    if transaction_id:
        for row in lifetime_rows:
            if row.store_transaction_id == transaction_id:
                return row
    same_product = [
        row
        for row in lifetime_rows
        if row.product_id == product_id and bool(row.is_sandbox) == sandbox
    ]
    entitled = (r for r in same_product if r.status in ENTITLED_SUBSCRIPTION_STATUSES)
    return next(entitled, same_product[0] if same_product else None)


def _restore_renewing_row(
    uow: Any,
    user: Any,
    subscriber_id: str,
    event: dict[str, Any],
    rows: list[Any],
    refunded: Any,
    current: dict[str, Any],
) -> Any | None:
    """Give a renewing plan still paid for its row back after a lifetime refund.

    An upgrade converted the yearly row, so refunding the upgrade must not
    leave someone who still pays yearly with only a refunded row.
    """
    row = apply_subscription_info(uow, user, event, subscriber_id, rows, current)
    if row is None:  # only while a lifetime row grants access, which it no longer does
        return None
    if all(row is not other for other in rows):
        # Not the refund's transaction, so a redelivered refund can't match it.
        row.store_transaction_id = None
        row.purchased_at = refunded.purchased_at
    logger.info(
        "Restored %s after a lifetime refund: user_id=%s", row.product_id, user.id
    )
    return row

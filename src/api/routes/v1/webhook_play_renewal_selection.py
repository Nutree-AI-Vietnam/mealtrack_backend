"""Which Play plans a Play lifetime holder's webhook asks Google to stop.

Every renewing Play plan is picked, not only the yearly. That is intended:
the app has one entitlement, ``standard``, which lifetime grants for good, so
a weekly, monthly or yearly plan beside it adds nothing and would only
charge again.

Our rows only nominate plans; Google decides. A failed renewal arrives as
BILLING_ISSUE and as CANCELLATION (billing error) while Play keeps retrying
the charge, and a restored plan's UNCANCELLATION isn't stored, so a row's
status and expiry can't tell whether the plan still renews. The adapter
reads Play's own state and cancels only a plan that still auto-renews.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.api.routes.v1.webhook_lookup_parsing import store_platform
from src.api.routes.v1.webhook_subscription_rows import (
    is_sandbox_event,
    load_subscriber_rows,
)
from src.domain.constants.lifetime_products import grants_access, is_lifetime_product
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

# Longer than Play's grace period plus account hold, during which a plan
# whose stored expiry has passed may still be charged.
RENEWAL_LOOKBACK = timedelta(days=90)


@dataclass(frozen=True)
class PlayRenewal:
    """A Play plan to stop, as it was before the purchase changed any row."""

    user_id: str
    product_id: str
    order_id: str | None  # RevenueCat's Play transaction id is the order id


def play_order_base(order_id: str) -> str:
    """The subscription's first order id; each renewal appends ``..N``."""
    return order_id.split("..", 1)[0]


def play_renewals_to_cancel(
    rows: list[Any], user: Any, event: dict[str, Any]
) -> list[PlayRenewal]:
    """The event user's Play plans that may still renew, for a Play purchase.

    Plan rows of the event's environment that aren't refunded and whose
    expiry is unknown, ahead, or within the lookback, one per subscription.
    """
    if store_platform(event.get("store")) != "android":
        return []
    sandbox = is_sandbox_event(event)
    cutoff = utc_now() - RENEWAL_LOOKBACK
    picked: dict[str, PlayRenewal] = {}
    for row in rows:
        if not (
            row.user_id == user.id
            and row.platform == "android"
            and bool(row.is_sandbox) == sandbox
            and not is_lifetime_product(row.product_id)
            and row.status != "refunded"
            and _ends_after(row.expires_at, cutoff)
        ):
            continue
        order_id = row.store_transaction_id or None
        key = play_order_base(order_id) if order_id else f"row:{row.id}"
        picked.setdefault(key, PlayRenewal(user.id, row.product_id, order_id))
    if not picked:
        logger.info("Play lifetime purchase: no Play plan to stop: user_id=%s", user.id)
    return list(picked.values())


async def play_plan_beside_lifetime(
    uow: Any, user: Any, event: dict[str, Any]
) -> list[PlayRenewal]:
    """A Play plan bought or renewed while the event user holds a Play lifetime.

    Covers the upgrade's webhook landing before the yearly's, when the yearly
    had no row to pick. The event's own transaction id is the plan's order
    id. Read after the plan handler ran, under its subscriber lock.
    """
    product_id = event.get("product_id")
    if (
        store_platform(event.get("store")) != "android"
        or not isinstance(product_id, str)
        or is_lifetime_product(product_id)
    ):
        return []
    rows = await load_subscriber_rows(uow, event.get("app_user_id"))
    sandbox = is_sandbox_event(event)
    now = utc_now()
    holds_play_lifetime = any(
        row.user_id == user.id
        and row.platform == "android"
        and bool(row.is_sandbox) == sandbox
        and is_lifetime_product(row.product_id)
        and grants_access(row.status, row.product_id, row.expires_at, now)
        for row in rows
    )
    if not holds_play_lifetime:
        return []
    order_id = event.get("transaction_id") or event.get("original_transaction_id")
    logger.info(
        "Play plan event beside a Play lifetime: user_id=%s event_type=%s product=%s",
        user.id,
        event.get("type"),
        product_id,
    )
    return [
        PlayRenewal(
            user.id, product_id, order_id if isinstance(order_id, str) else None
        )
    ]


def _ends_after(expires_at: datetime | None, cutoff: datetime) -> bool:
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:  # SQLite drops the zone; stored values are UTC
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > cutoff

"""Store products that grant the ``standard`` entitlement for life.

Both are one-time purchases with the same id on the App Store and Google
Play. A ``:suffix`` (a Play base plan or purchase option) is dropped before
matching, the same rule the streak-pack matcher uses, and rows store the base
id. This module also says which subscriptions rows grant access.
"""

from __future__ import annotations

from datetime import UTC, datetime

# 1.299.000 VND, bought outright.
LIFETIME_PRODUCT_ID = "nutree_lifetime_1299k"
# 700.000 VND, offered right after a yearly purchase to upgrade it to lifetime.
LIFETIME_UPGRADE_PRODUCT_ID = "nutree_lifetime_upgrade_700k"

LIFETIME_PRODUCT_IDS: frozenset[str] = frozenset(
    {LIFETIME_PRODUCT_ID, LIFETIME_UPGRADE_PRODUCT_ID}
)

# Statuses whose row alone proves access. "cancelled" is left to RevenueCat: a
# store refund arrives as a CANCELLATION webhook too, so a cancelled row can't
# tell "paid until expiry" from "refunded".
ENTITLED_SUBSCRIPTION_STATUSES = ("active", "billing_issue")


def lifetime_base_id(product_id: object) -> str | None:
    """The lifetime product id without any ``:suffix``; None for other products."""
    if not isinstance(product_id, str):
        return None
    base = product_id.split(":", 1)[0]
    return base if base in LIFETIME_PRODUCT_IDS else None


def is_lifetime_product(product_id: object) -> bool:
    """Whether the product id names a lifetime purchase."""
    return lifetime_base_id(product_id) is not None


def grants_access(
    status: object, product_id: object, expires_at: datetime | None, now: datetime
) -> bool:
    """Whether a row with these values proves access at ``now``.

    The same rule as the Premium gate's database check: an entitled status,
    and an expiry still ahead or no expiry on a lifetime product.
    """
    if status not in ENTITLED_SUBSCRIPTION_STATUSES:
        return False
    if expires_at is None:
        return is_lifetime_product(product_id)
    if expires_at.tzinfo is None:  # SQLite drops the zone; stored values are UTC
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > now

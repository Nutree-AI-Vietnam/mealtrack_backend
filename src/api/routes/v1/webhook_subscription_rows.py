"""Which subscriptions row a RevenueCat event acts on.

A subscriber can hold several rows: web-funnel claims write one per product,
transfers move rows between subscribers, and a lifetime purchase adds one or
converts the yearly row it upgrades. Handlers pick a row on purpose here
instead of taking whichever row a query returns first.

Renewing-product events never change a lifetime row. While a lifetime row
grants access they change only their own product's row, never another or a
new one: someone who upgraded keeps lifetime whatever then happens to the
yearly plan, and a plan bought beside lifetime still tracks its own state.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from src.api.routes.v1.webhook_lookup_parsing import (
    parse_platform,
    parse_revenuecat_expiry,
    parse_timestamp,
    store_platform,
)
from src.domain.constants.lifetime_products import (
    LIFETIME_UPGRADE_PRODUCT_ID,
    grants_access,
    is_lifetime_product,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

logger = logging.getLogger(__name__)


def is_sandbox_event(event: dict[str, Any]) -> bool:
    return event.get("environment") == "SANDBOX"


async def lock_subscriber(uow: Any, subscriber_id: object) -> None:
    """Serialize webhook writes for one subscriber until the transaction ends.

    Postgres only: other databases (the unit tests' SQLite) have no advisory
    locks, and the lock adds nothing there.
    """
    if not isinstance(subscriber_id, str) or not subscriber_id:
        return
    bind = uow.session.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        return
    await uow.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:subscriber_id))"),
        {"subscriber_id": subscriber_id},
    )


async def load_subscriber_rows(uow: Any, subscriber_id: object) -> list[Any]:
    """Every row of the subscriber, newest purchase first; none without an id."""
    if not isinstance(subscriber_id, str) or not subscriber_id:
        return []
    return list(await uow.subscriptions.find_all_by_revenuecat_id(subscriber_id))


def _grants_access_now(row: Any, now: datetime) -> bool:
    return grants_access(row.status, row.product_id, row.expires_at, now)


def entitled_lifetime_row(rows: list[Any], *, sandbox: bool) -> Any | None:
    """A lifetime row of the event's environment that still grants access."""
    now = utc_now()
    return next(
        (
            row
            for row in rows
            if is_lifetime_product(row.product_id)
            and bool(row.is_sandbox) == sandbox
            and _grants_access_now(row, now)
        ),
        None,
    )


def renewing_row(rows: list[Any], product_id: object) -> Any | None:
    """The row of this renewing product, else the newest renewing row."""
    renewing = [row for row in rows if not is_lifetime_product(row.product_id)]
    same_product = (row for row in renewing if row.product_id == product_id)
    return next(same_product, renewing[0] if renewing else None)


def has_paid_access(
    rows: list[Any], *, sandbox: bool, exclude: Any | None = None
) -> bool:
    """Whether any row of the environment, other than ``exclude``, grants access."""
    now = utc_now()
    return any(
        row is not exclude
        and bool(row.is_sandbox) == sandbox
        and _grants_access_now(row, now)
        for row in rows
    )


def had_paid_purchase(rows: list[Any]) -> bool:
    """Whether an earlier purchase was already reported as the initial one.

    Refunded rows don't count. Nor does the upgrade: it is only ever bought on
    top of a yearly plan, and that plan's own INITIAL_PURCHASE is the one
    reported, whichever webhook arrives first.
    """
    return any(
        row.status != "refunded" and row.product_id != LIFETIME_UPGRADE_PRODUCT_ID
        for row in rows
    )


async def keeps_paid_access(uow: Any, event: dict[str, Any], exclude: Any) -> bool:
    """Whether the subscriber still has paid access once ``exclude`` is refunded."""
    rows = await load_subscriber_rows(uow, event.get("app_user_id"))
    return has_paid_access(rows, sandbox=is_sandbox_event(event), exclude=exclude)


async def lifetime_holds_access(uow: Any, event: dict[str, Any]) -> bool:
    """Whether a lifetime row of the event's environment grants access."""
    rows = await load_subscriber_rows(uow, event.get("app_user_id"))
    return entitled_lifetime_row(rows, sandbox=is_sandbox_event(event)) is not None


@dataclass(frozen=True)
class RenewingTarget:
    """What an event about a renewing product may change."""

    row: Any | None  # the plan's row; None when there is none yet
    # A lifetime row grants access: only the plan's own row may change, no row
    # is created, and no payment-failed push goes out.
    kept_for_lifetime: bool
    had_paid_purchase: bool


async def find_renewing_target(
    uow: Any, user: Any, event: dict[str, Any]
) -> RenewingTarget:
    # Locked before reading, so a quick "turn off renewal" cancellation waits
    # for an upgrade converting the same row instead of overwriting it.
    await lock_subscriber(uow, event.get("app_user_id"))
    rows = await load_subscriber_rows(uow, event.get("app_user_id"))
    paid = had_paid_purchase(rows)
    product_id = event.get("product_id")
    if is_lifetime_product(product_id):
        # The router sends lifetime events to their own handlers.
        logger.error(
            "Lifetime event reached a subscription handler: type=%s",
            event.get("type"),
        )
        return RenewingTarget(None, True, paid)
    held = entitled_lifetime_row(rows, sandbox=is_sandbox_event(event))
    if held is not None:
        own = users_row_first([r for r in rows if r.product_id == product_id], user)
        if own is not None:
            take_over(own, user)
        logger.info(
            "Lifetime holds access; plan event changes only its own row: "
            "user_id=%s event_type=%s event_product=%s own_row=%s",
            user.id,
            event.get("type"),
            product_id,
            own is not None,
        )
        return RenewingTarget(own, True, paid)
    return RenewingTarget(renewing_row(rows, product_id), False, paid)


def users_row_first(rows: list[Any], user: Any) -> Any | None:
    """The event user's row among ``rows``, else the newest one.

    One store account can hold rows for two Nutree accounts (same subscriber
    id, same product), so the event's own user is preferred.
    """
    own = (row for row in rows if row.user_id == user.id)
    return next(own, rows[0] if rows else None)


def take_over(row: Any, user: Any) -> None:
    """The event user takes the row over, as the redemption sync does."""
    if row.user_id != user.id:
        row.user_id = user.id
        row.updated_at = utc_now()


def new_subscription_row(
    user: Any,
    event: dict[str, Any],
    *,
    product_id: str | None,
    expires_at: datetime | None,
    subscriber_id: str | None = None,
    store: str | None = None,
) -> Subscription:
    """An active row for the event's subscriber, store and environment."""
    return Subscription(
        id=str(uuid.uuid4()),
        user_id=user.id,
        revenuecat_subscriber_id=subscriber_id or event.get("app_user_id"),
        product_id=product_id,
        platform=parse_platform(store or event.get("store")),
        status="active",
        purchased_at=parse_timestamp(event.get("purchased_at_ms")) or utc_now(),
        expires_at=expires_at,
        store_transaction_id=event.get("transaction_id"),
        is_sandbox=is_sandbox_event(event),
    )


def apply_subscription_info(
    uow: Any,
    user: Any,
    event: dict[str, Any],
    subscriber_id: str,
    rows: list[Any],
    current: dict[str, Any],
    *,
    lifetime_held: bool = False,
) -> Any | None:
    """Point the plan's row at the renewing plan RevenueCat reports as active.

    Only plan rows of the event's environment and the plan's platform qualify,
    so a web-funnel or sandbox row is never overwritten: the plan's own row
    (the event user's first), else another such row, else a new row. While a
    lifetime row grants access only the plan's own row is updated and none is
    added. The row updated belongs to the event user from then on.
    """
    store = current.get("store") or event.get("store")
    platform = store_platform(store)  # None: a store with no certain platform
    sandbox = is_sandbox_event(event)
    candidates = [
        row
        for row in rows
        if not is_lifetime_product(row.product_id)
        and bool(row.is_sandbox) == sandbox
        and (platform is None or row.platform == platform)
    ]
    same_product = [r for r in candidates if r.product_id == current.get("product_id")]
    subscription = users_row_first(same_product, user)
    if subscription is None and not lifetime_held:
        subscription = users_row_first(candidates, user)
    if subscription is None and lifetime_held:
        return None
    if subscription is None:
        subscription = new_subscription_row(
            user,
            event,
            product_id=current.get("product_id")
            or event.get("product_id")
            or "unknown",
            expires_at=parse_revenuecat_expiry(current.get("expires_date")),
            subscriber_id=subscriber_id,
            store=current.get("store"),
        )
        uow.session.add(subscription)
        return subscription
    take_over(subscription, user)
    subscription.product_id = current.get("product_id") or subscription.product_id
    if platform is None:  # recorded as before the platform filter existed
        subscription.platform = parse_platform(store)
    subscription.status = "active"
    subscription.expires_at = parse_revenuecat_expiry(current.get("expires_date"))
    subscription.updated_at = utc_now()
    return subscription


async def get_or_create_subscription(uow: Any, user: Any, event: dict[str, Any]):
    """The renewing plan's row, created if missing (a missed INITIAL_PURCHASE).

    While a lifetime row grants access, only the plan's own row is returned,
    and None when it has none: no row is created.
    """
    target = await find_renewing_target(uow, user, event)
    if target.row is not None or target.kept_for_lifetime:
        return target.row
    logger.warning(
        f"No subscription found for user {user.id}, creating record (missed INITIAL_PURCHASE)"
    )
    subscription = new_subscription_row(
        user,
        event,
        product_id=event.get("product_id") or "unknown",
        expires_at=parse_timestamp(event.get("expiration_at_ms")),
    )
    uow.session.add(subscription)
    await uow.session.flush()
    return subscription

"""Turn off the buyer's Play plan renewals once a Play lifetime purchase is stored.

Neither store credits a running plan against lifetime, so a plan left
renewing charges again next period. Play lets the developer stop it; the
App Store doesn't, so only purchases made on Play get here.

The plans are picked inside the purchase's unit of work, before the upgrade
converts the yearly row, and are cancelled only after that unit commits:
the lifetime grant never waits on Google or fails because of it. Nothing
here raises, so RevenueCat never redelivers the purchase because of it. A
redelivered purchase is already stored and picks no plans, so a duplicate
webhook cancels nothing twice.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from src.api.routes.v1.webhook_lookup_parsing import store_platform
from src.api.routes.v1.webhook_subscription_rows import is_sandbox_event
from src.bootstrap.integration_services import get_play_subscription_service
from src.domain.constants.lifetime_products import grants_access, is_lifetime_product
from src.domain.ports.play_subscription_port import (
    PlayBillingError,
    PlaySubscriptionPort,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

# Well inside RevenueCat's 60 s webhook timeout: a slow Google never turns a
# stored purchase into a redelivery.
CANCEL_BUDGET_SECONDS = 10.0


@dataclass(frozen=True)
class PlayRenewal:
    """A Play plan row that still renews, as it was before the purchase."""

    row_id: str
    user_id: str
    product_id: str
    order_id: str | None  # RevenueCat's Play transaction id is the order id


def _get_play_subscription_service() -> PlaySubscriptionPort:
    return get_play_subscription_service()


def play_renewals_to_cancel(
    rows: list[Any], user: Any, event: dict[str, Any]
) -> list[PlayRenewal]:
    """The event user's Play plans that still renew, for a Play purchase.

    Only plan rows of the event's environment that grant access now: active or
    billing-issue, with the period still ahead. A cancelled row has already
    stopped renewing, and another account's row is not this buyer's plan.
    """
    if store_platform(event.get("store")) != "android":
        return []
    sandbox = is_sandbox_event(event)
    now = utc_now()
    renewals = [
        PlayRenewal(row.id, row.user_id, row.product_id, row.store_transaction_id)
        for row in rows
        if row.user_id == user.id
        and row.platform == "android"
        and bool(row.is_sandbox) == sandbox
        and not is_lifetime_product(row.product_id)
        and grants_access(row.status, row.product_id, row.expires_at, now)
    ]
    if not renewals:
        logger.info(
            "Play lifetime purchase: no renewing Play plan: user_id=%s", user.id
        )
    return renewals


async def cancel_play_renewals(
    event: dict[str, Any], renewals: list[PlayRenewal]
) -> None:
    """Cancel each renewal once the purchase is committed; logs, never raises."""
    if not renewals:
        return
    user_id = renewals[0].user_id
    try:
        service = _get_play_subscription_service()
    except Exception:
        logger.exception("Play renewal cancel could not start: user_id=%s", user_id)
        return
    if not service.enabled:
        logger.warning(
            "Play renewal cancel is off; %d Play plan(s) keep renewing: user_id=%s",
            len(renewals),
            user_id,
        )
        return
    if is_sandbox_event(event) and not service.sandbox_enabled:
        logger.info(
            "Play renewal cancel skipped for a sandbox purchase: user_id=%s", user_id
        )
        return
    try:
        async with asyncio.timeout(CANCEL_BUDGET_SECONDS):
            for renewal in renewals:
                await _cancel(service, renewal)
    except TimeoutError:
        logger.error(
            "Play renewal cancel ran out of time: user_id=%s budget_s=%s",
            user_id,
            CANCEL_BUDGET_SECONDS,
        )


async def _cancel(service: PlaySubscriptionPort, renewal: PlayRenewal) -> None:
    if not renewal.order_id:
        logger.error(
            "Play renewal not cancelled, its row has no order id: "
            "user_id=%s row_id=%s product=%s",
            renewal.user_id,
            renewal.row_id,
            renewal.product_id,
        )
        return
    try:
        await service.cancel_renewal(renewal.order_id)
    except PlayBillingError as exc:
        logger.error(
            "Play renewal cancel failed: user_id=%s row_id=%s product=%s "
            "error=%s status=%s detail=%s",
            renewal.user_id,
            renewal.row_id,
            renewal.product_id,
            type(exc).__name__,
            exc.status_code,
            exc,
        )
        return
    except Exception:
        logger.exception(
            "Play renewal cancel failed unexpectedly: user_id=%s row_id=%s product=%s",
            renewal.user_id,
            renewal.row_id,
            renewal.product_id,
        )
        return
    logger.info(
        "Play renewal cancelled after a lifetime purchase: "
        "user_id=%s row_id=%s product=%s",
        renewal.user_id,
        renewal.row_id,
        renewal.product_id,
    )

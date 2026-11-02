"""Stop the Play plans a Play lifetime holder's webhook picked, after it commits.

Neither store credits a running plan against lifetime, so a plan left
renewing charges again next period. Play lets the developer stop it; the
App Store doesn't. ``webhook_play_renewal_selection`` picks the plans inside
the event's unit of work; this runs only after that unit commits, so the
grant never waits on Google or fails because of it.

Each plan is cancelled only when RevenueCat lists its order under the
event's subscriber, so a plan our rows attribute to the buyer but the store
account doesn't (a broad transfer) is never touched. Nothing here raises, so
RevenueCat never redelivers an event because of it. Failures are logged at
ERROR, without a traceback, with the order id needed to cancel by hand.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from src.api.base_dependencies import get_subscription_service
from src.api.routes.v1.webhook_play_renewal_selection import (
    PlayRenewal,
    play_order_base,
)
from src.api.routes.v1.webhook_subscription_rows import is_sandbox_event
from src.bootstrap.integration_services import get_play_subscription_service
from src.domain.ports.play_subscription_port import (
    PlayBillingError,
    PlayCancelOutcome,
    PlaySubscriptionPort,
)

logger = logging.getLogger(__name__)

# Well inside RevenueCat's 60 s webhook timeout: a slow Google never turns a
# stored event into a redelivery. Covers the RevenueCat lookup, every
# Google call and the adapter's one retry.
CANCEL_BUDGET_SECONDS = 10.0
REVENUECAT_LOOKUP_SECONDS = 3.0


def _get_play_subscription_service() -> PlaySubscriptionPort:
    return get_play_subscription_service()


def _get_subscription_service() -> Any:
    return get_subscription_service()


def _plans(renewals: Sequence[PlayRenewal]) -> str:
    """Our user id and each plan's order and product, for cancelling by hand."""
    user_id = renewals[0].user_id if renewals else None
    plans = ";".join(f"{r.order_id}|{r.product_id}" for r in renewals)
    return f"user_id={user_id} plans={plans}"


async def cancel_play_renewals(
    event: dict[str, Any], renewals: list[PlayRenewal]
) -> None:
    """Cancel each renewal once the event is committed; logs, never raises."""
    if not renewals:
        return
    try:
        service = _get_play_subscription_service()
    except Exception as exc:
        logger.error(
            "Play renewal cancel could not start: error=%s %s",
            type(exc).__name__,
            _plans(renewals),
        )
        return
    if not service.enabled:
        if service.switched_on:
            logger.error(
                "Play renewal cancel is on but unusable; plans keep renewing: %s",
                _plans(renewals),
            )
        else:
            logger.warning(
                "Play renewal cancel is off; %d Play plan(s) keep renewing: user_id=%s",
                len(renewals),
                renewals[0].user_id,
            )
        return
    if is_sandbox_event(event) and not service.sandbox_enabled:
        logger.info(
            "Play renewal cancel skipped for a sandbox event: user_id=%s",
            renewals[0].user_id,
        )
        return
    pending = list(renewals)
    try:
        async with asyncio.timeout(CANCEL_BUDGET_SECONDS):
            owned = await _subscriber_play_orders(event)
            while pending:
                await _cancel(service, pending[0], owned)
                pending.pop(0)
    except TimeoutError:
        logger.error(
            "Play renewal cancel ran out of time: budget_s=%s %s",
            CANCEL_BUDGET_SECONDS,
            _plans(pending),
        )


async def _subscriber_play_orders(event: dict[str, Any]) -> set[str] | None:
    """The base order ids RevenueCat lists under the event's subscriber.

    None when RevenueCat can't answer: nothing is cancelled unconfirmed.
    """
    subscriber_id = event.get("app_user_id")
    if not isinstance(subscriber_id, str) or not subscriber_id:
        return None
    try:
        lookup = _get_subscription_service().get_subscriber_info(subscriber_id)
        info = await asyncio.wait_for(lookup, timeout=REVENUECAT_LOOKUP_SECONDS)
    except Exception as exc:
        logger.warning(
            "RevenueCat lookup for Play renewal cancel failed: error=%s",
            type(exc).__name__,
        )
        return None
    subscriber = info.get("subscriber") if isinstance(info, dict) else None
    entries = subscriber.get("subscriptions") if isinstance(subscriber, dict) else None
    if not isinstance(entries, dict):
        return None
    return {
        play_order_base(entry["store_transaction_id"])
        for entry in entries.values()
        if isinstance(entry, dict)
        and str(entry.get("store") or "").lower() == "play_store"
        and isinstance(entry.get("store_transaction_id"), str)
        and entry["store_transaction_id"]
    }


async def _cancel(
    service: PlaySubscriptionPort, renewal: PlayRenewal, owned: set[str] | None
) -> None:
    plan = _plans([renewal])
    if not renewal.order_id:
        logger.error("Play renewal not cancelled, its row has no order id: %s", plan)
        return
    if owned is None:
        logger.error(
            "Play renewal not cancelled, RevenueCat could not confirm the owner: %s",
            plan,
        )
        return
    if play_order_base(renewal.order_id) not in owned:
        logger.error(
            "Play renewal not cancelled, RevenueCat lists the order under "
            "another subscriber: %s",
            plan,
        )
        return
    try:
        outcome = await service.cancel_renewal(renewal.order_id, renewal.product_id)
    except PlayBillingError as exc:
        logger.error(
            "Play renewal cancel failed: error=%s status=%s detail=%s %s",
            type(exc).__name__,
            exc.status_code,
            exc,
            plan,
        )
        return
    except Exception as exc:
        logger.error(
            "Play renewal cancel failed unexpectedly: error=%s %s",
            type(exc).__name__,
            plan,
        )
        return
    if outcome is PlayCancelOutcome.NOT_RENEWING:
        logger.info(
            "Play renewal already off or ended: user_id=%s product=%s",
            renewal.user_id,
            renewal.product_id,
        )
    else:
        logger.info(
            "Play renewal cancelled for a Play lifetime holder: user_id=%s product=%s",
            renewal.user_id,
            renewal.product_id,
        )

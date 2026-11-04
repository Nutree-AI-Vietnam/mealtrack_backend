"""RevenueCat webhook events for streak restore packs.

Packs are consumables on the restore ledger: a purchase credits it and a
refund debits the pack's owner. No event for a pack reaches the subscription
handlers, whatever its type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.app.services.streak_restore_ledger import (
    PackPurchase,
    credit_pack,
    refund_pack,
)
from src.domain.model.streak import (
    StreakRestorePolicy,
    mentions_streak_restore,
    restore_count_for,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreakRestoreWebhookResult:
    """A pack event, handled. ``owner_id`` holds the ledger the event acted on.

    None when the event was acknowledged without reaching the ledger.
    """

    owner_id: str | None = None


async def handle_streak_restore_event(
    uow: AsyncUnitOfWorkPort,
    user: Any,  # the event's User row; only its id is read
    event: dict[str, Any],
    policy: StreakRestorePolicy,
) -> StreakRestoreWebhookResult | None:
    """Apply a pack event to the ledger in the caller's unit of work.

    Returns None when the event is not about a pack, so the caller carries on
    with the subscription handlers.
    """
    product_id = event.get("product_id")
    if not isinstance(product_id, str) or not mentions_streak_restore(product_id):
        return None
    event_type = event.get("type")
    count = restore_count_for(product_id)
    if count is None:
        logger.error(
            "streak_restore_webhook_unknown_product type=%s product=%s",
            event_type,
            product_id,
        )
        return StreakRestoreWebhookResult()
    transaction_id = event.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        logger.error(
            "streak_restore_webhook_missing_transaction type=%s product=%s",
            event_type,
            product_id,
        )
        return StreakRestoreWebhookResult()
    purchase = PackPurchase(
        product_id=product_id,
        store_transaction_id=transaction_id,
        count=count,
        store=_lower(event.get("store")) or "unknown",
        environment=_lower(event.get("environment")),
    )
    if purchase.environment == "sandbox" and not policy.allows_sandbox(user.id):
        logger.info(
            "streak_restore_webhook_sandbox_ignored type=%s user_id=%s transaction=%s",
            event_type,
            user.id,
            transaction_id,
        )
        return StreakRestoreWebhookResult()

    if event_type == "NON_RENEWING_PURCHASE":
        await uow.streak_restores.lock_user(user.id)
        credit = await credit_pack(uow, user.id, purchase)
        if credit.owner_id != user.id:
            logger.warning(
                "streak_restore_webhook_owner_mismatch transaction=%s owner=%s "
                "event_user=%s",
                transaction_id,
                credit.owner_id,
                user.id,
            )
        logger.info(
            "streak_restore_webhook_credited user_id=%s count=%s inserted=%s "
            "transaction=%s",
            credit.owner_id,
            credit.count,
            credit.inserted,
            transaction_id,
        )
        return StreakRestoreWebhookResult(credit.owner_id)

    if event_type == "CANCELLATION":
        # Every pack cancellation is a refund: a consumable has nothing to renew.
        refund = await refund_pack(uow, user.id, purchase)
        logger.info(
            "streak_restore_webhook_refunded user_id=%s taken=%s inserted=%s "
            "transaction=%s",
            refund.owner_id,
            refund.count,
            refund.inserted,
            transaction_id,
        )
        return StreakRestoreWebhookResult(refund.owner_id)

    # REFUND_REVERSED does not re-credit a pack; no other type moves the ledger.
    logger.info(
        "streak_restore_webhook_ignored type=%s product=%s transaction=%s",
        event_type,
        product_id,
        transaction_id,
    )
    return StreakRestoreWebhookResult()


def _lower(value: object) -> str | None:
    return value.lower() if isinstance(value, str) and value else None

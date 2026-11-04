"""Verify a restore pack the app just bought before it is credited.

Runs outside any database transaction: no lock is held while RevenueCat
answers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from src.api.exceptions import (
    AuthorizationException,
    BusinessLogicException,
    ConflictException,
    ExternalServiceException,
    ResourceNotFoundException,
)
from src.app.commands.streak import PackClaim
from src.app.services.streak_restore_ledger import PackPurchase
from src.domain.model.streak import (
    LedgerKind,
    StreakRestorePolicy,
    restore_count_for,
)
from src.domain.ports.subscription_service_port import (
    OneTimePurchase,
    OneTimePurchaseLookup,
    SubscriptionServicePort,
)

logger = logging.getLogger(__name__)

LOOKUP_TIMEOUT_SECONDS = 3.0


async def verify_pack_claim(
    *,
    uow_factory: Callable[[], Any],
    subscriptions: SubscriptionServicePort,
    policy: StreakRestorePolicy,
    user_id: str,
    claim: PackClaim,
    retry_delay_seconds: float = 1.0,
) -> PackPurchase:
    """The claimed pack as the ledger or RevenueCat knows it, or the error to answer."""
    count = restore_count_for(claim.product_id)
    if count is None:
        raise BusinessLogicException(
            "This product is not a restore pack.", "UNKNOWN_RESTORE_PRODUCT"
        )
    async with uow_factory() as uow:
        user = await uow.users.find_by_id(user_id)
        held = await uow.streak_restores.find_transaction(
            claim.transaction_id, LedgerKind.PURCHASE
        )
    if held is not None:
        # Already on the ledger (the webhook or an earlier claim). RevenueCat
        # may no longer list it, after a refund or a transfer, so it is not
        # asked again; the row passed the sandbox check when it was written.
        if held.user_id != user_id:
            raise ConflictException(
                "This purchase belongs to another account.",
                "PURCHASE_OWNED_BY_ANOTHER_ACCOUNT",
            )
        return PackPurchase(
            product_id=held.product_id or claim.product_id,
            store_transaction_id=claim.transaction_id,
            count=held.delta,
            store=held.store,
            environment=held.environment,
        )
    # Paused claims still answer for a pack the ledger holds (the webhook
    # credited it), so the app's pending claim can clear.
    if not policy.claim_enabled:
        raise ExternalServiceException(
            "Restore purchases are paused.", "FEATURE_DISABLED"
        )
    firebase_uid = getattr(user, "firebase_uid", None)
    if not firebase_uid:
        raise ResourceNotFoundException("User not found.", "USER_NOT_FOUND")

    async def lookup() -> OneTimePurchase:
        # httpx times each phase apart; this bounds the whole lookup.
        try:
            async with asyncio.timeout(LOOKUP_TIMEOUT_SECONDS):
                return await subscriptions.find_one_time_purchase(
                    firebase_uid,
                    claim.product_id,
                    claim.transaction_id,
                    timeout_seconds=LOOKUP_TIMEOUT_SECONDS,
                )
        except TimeoutError:
            return OneTimePurchase(OneTimePurchaseLookup.UNAVAILABLE)

    found = await lookup()
    if found.status is OneTimePurchaseLookup.NOT_FOUND:
        # The store can answer before RevenueCat has the receipt.
        await asyncio.sleep(retry_delay_seconds)
        found = await lookup()
    if found.status is OneTimePurchaseLookup.UNAVAILABLE:
        raise ExternalServiceException(
            "The purchase could not be verified right now.",
            "PURCHASE_VERIFICATION_UNAVAILABLE",
        )
    if found.status is OneTimePurchaseLookup.NOT_FOUND:
        raise ResourceNotFoundException(
            "The purchase was not found.", "PURCHASE_NOT_FOUND"
        )
    if found.is_sandbox and not policy.allows_sandbox(user_id):
        logger.info("streak_restore_sandbox_claim_refused user_id=%s", user_id)
        raise AuthorizationException(
            "Test purchases do not count here.", "SANDBOX_PURCHASE_NOT_ALLOWED"
        )
    return PackPurchase(
        product_id=claim.product_id,
        store_transaction_id=claim.transaction_id,
        count=count,
        store=found.store,
        environment="sandbox" if found.is_sandbox else "production",
    )

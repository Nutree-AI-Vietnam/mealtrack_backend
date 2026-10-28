"""Restore missed streak days with the free, held or just-bought restores.

A bought pack is verified with the subscription provider before any database
lock is taken. Credit, spend and restored days then commit together under the
user's row lock, and the streak cache is purged after the commit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from itertools import pairwise
from typing import Any

from src.api.exceptions import (
    ConflictException,
    ExternalServiceException,
    ValidationException,
)
from src.app.commands.streak import RestoreStreakCommand
from src.app.events.base import EventHandler, handles
from src.app.services.streak_restore_ledger import (
    PackPurchase,
    credit_pack,
    spend_restores,
)
from src.app.services.streak_restore_purchase import verify_pack_claim
from src.app.services.streak_restore_state import (
    RestoreState,
    load_restore_state,
    purge_streak_cache,
    streak_payload,
)
from src.domain.model.streak import (
    LedgerKind,
    NotEnoughRestoresError,
    StreakRestorePolicy,
)
from src.domain.ports.cache_port import CachePort
from src.domain.ports.subscription_service_port import SubscriptionServicePort

logger = logging.getLogger(__name__)

MAX_RESTORE_DATES = 2


@handles(RestoreStreakCommand)
class RestoreStreakCommandHandler(EventHandler[RestoreStreakCommand, dict[str, Any]]):
    def __init__(
        self,
        uow_factory: Callable[[], Any],
        cache_service: CachePort | None,
        subscription_service: SubscriptionServicePort,
        policy: StreakRestorePolicy,
        retry_delay_seconds: float = 1.0,
    ):
        self.uow_factory = uow_factory
        self.cache_service = cache_service
        self.subscription_service = subscription_service
        self.policy = policy
        self.retry_delay_seconds = retry_delay_seconds

    async def handle(self, cmd: RestoreStreakCommand) -> dict[str, Any]:
        _validate_dates(cmd)
        if cmd.purchase is None and not self.policy.spend_enabled:
            raise _disabled("Streak restores are paused.")
        pack = None
        if cmd.purchase is not None:
            # Verified before the transaction: no lock is held during HTTP.
            pack = await verify_pack_claim(
                uow_factory=self.uow_factory,
                subscriptions=self.subscription_service,
                policy=self.policy,
                user_id=cmd.user_id,
                claim=cmd.purchase,
                retry_delay_seconds=self.retry_delay_seconds,
            )
        async with self.uow_factory() as uow:
            result = await self._apply(uow, cmd, pack)
        await purge_streak_cache(self.cache_service, cmd.user_id)
        return result

    async def _apply(
        self, uow: Any, cmd: RestoreStreakCommand, pack: PackPurchase | None
    ) -> dict[str, Any]:
        await uow.streak_restores.lock_user(cmd.user_id)
        credited = 0
        if pack is not None:
            credit = await credit_pack(uow, cmd.user_id, pack)
            if credit.owner_id != cmd.user_id:
                logger.warning(
                    "streak_restore_claim_owned_by_another transaction=%s owner=%s caller=%s",
                    pack.store_transaction_id,
                    credit.owner_id,
                    cmd.user_id,
                )
                raise ConflictException(
                    "This purchase belongs to another account.",
                    "PURCHASE_OWNED_BY_ANOTHER_ACCOUNT",
                )
            refunded = await uow.streak_restores.find_transaction(
                pack.store_transaction_id, LedgerKind.REFUND
            )
            # A refunded pack adds nothing, whatever it once credited.
            credited = 0 if refunded is not None else credit.count
        state = await self._state(uow, cmd)
        dates = cmd.dates
        if dates and set(dates) <= state.restored_dates:
            return self._result("restored", dates, credited, state)
        offer = state.calendar.offer if self.policy.spend_enabled else None
        if dates and offer is not None and tuple(dates) == offer.missed_dates:
            available = state.balance + (1 if state.free_available else 0)
            if len(dates) <= available:
                try:
                    restored = await spend_restores(uow, cmd.user_id, dates)
                except NotEnoughRestoresError:
                    restored = None
                if restored is not None:
                    state = await self._state(uow, cmd)
                    return self._result("restored", restored, credited, state)
            if pack is None:
                raise ConflictException(
                    "Not enough restores left.",
                    "NO_RESTORES_LEFT",
                    {"needed": len(dates), "available": available},
                )
        elif pack is None:
            raise ConflictException(
                "These days can no longer be restored.",
                "STREAK_NOT_RESTORABLE",
                {
                    "missed_dates": [
                        day.isoformat() for day in (offer.missed_dates if offer else ())
                    ]
                },
            )
        return self._result("credited_only", [], credited, state)

    async def _state(self, uow: Any, cmd: RestoreStreakCommand) -> RestoreState:
        return await load_restore_state(
            uow, cmd.user_id, cmd.header_timezone, self.policy
        )

    def _result(
        self, outcome: str, restored: list[date], credited: int, state: RestoreState
    ) -> dict[str, Any]:
        return {
            "outcome": outcome,
            "restored_dates": [day.isoformat() for day in restored],
            "credited": credited,
            "streak": streak_payload(state, self.policy),
        }


def _validate_dates(cmd: RestoreStreakCommand) -> None:
    dates = cmd.dates
    if not dates and cmd.purchase is None:
        raise ValidationException("Send the days to restore or a purchase.")
    if len(dates) > MAX_RESTORE_DATES:
        raise ValidationException(f"At most {MAX_RESTORE_DATES} days can be restored.")
    if any(later <= earlier for earlier, later in pairwise(dates)):
        raise ValidationException("Days must be unique and in ascending order.")


def _disabled(message: str) -> ExternalServiceException:
    return ExternalServiceException(message, "FEATURE_DISABLED")

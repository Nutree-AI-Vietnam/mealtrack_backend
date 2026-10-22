"""Turn paid MoMo orders into RevenueCat access windows for a Nutree user.

A grant never calls RevenueCat inside a transaction or under a row lock:

1. Reserve: under the user and order row locks, persist the order's access
   window, stacked after the user's other reserved or granted windows, and
   commit.
2. Call RevenueCat with the persisted end time. A retry sends the same end
   time, which RevenueCat treats as a duplicate, so retries never extend access.
3. Confirm: stamp ``granted_at`` once and claim the quiz lead.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import next_access_window
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_orders import mark_lead_claimed, normalize_email

logger = logging.getLogger(__name__)

STANDARD_ENTITLEMENT = "standard"


@dataclass(frozen=True)
class _Reservation:
    firebase_uid: str
    ends_at: datetime
    lead_id: str | None
    granted: bool


class MomoPassEntitlements:
    """Grants are idempotent: an order gets one window and is granted once."""

    def __init__(self, revenuecat):
        self._revenuecat = revenuecat

    async def grant(self, db: AsyncSession, order_id: str, user_id: str) -> bool:
        """Grant one paid order to a user; False leaves it for a later retry."""
        reservation = await self._reserve(db, order_id, user_id)
        if reservation is None:
            return False
        if reservation.granted:
            return True
        granted = await self._revenuecat.grant_promotional_entitlement(
            reservation.firebase_uid,
            STANDARD_ENTITLEMENT,
            int(reservation.ends_at.timestamp() * 1000),
        )
        if not granted:
            logger.warning("MoMo pass grant deferred for order %s", order_id)
            return False
        await self._confirm(db, order_id, reservation)
        logger.info("MoMo pass granted for order %s", order_id)
        return True

    async def _reserve(
        self, db: AsyncSession, order_id: str, user_id: str
    ) -> _Reservation | None:
        """Persist the order's window under row locks; None when it is not paid."""
        # Locked reads overwrite the identity map: a stale granted_at or window
        # would let a waiting request grant the order again.
        user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
        order = await db.scalar(
            select(MomoPaymentOrder)
            .where(MomoPaymentOrder.order_id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not user or not order or order.status != "paid":
            await db.rollback()
            return None
        reserved = order.granted_user_id == user.id and order.access_ends_at
        if not order.granted_at and not reserved:
            current_end = await db.scalar(
                select(func.max(MomoPaymentOrder.access_ends_at)).where(
                    MomoPaymentOrder.granted_user_id == user.id
                )
            )
            starts, ends = next_access_window(
                utc_now(), current_end, timedelta(weeks=order.weeks)
            )
            order.granted_user_id = user.id
            order.access_starts_at, order.access_ends_at = starts, ends
        reservation = _Reservation(
            firebase_uid=user.firebase_uid,
            ends_at=order.access_ends_at,
            lead_id=order.lead_id,
            granted=order.granted_at is not None,
        )
        await db.commit()
        return reservation

    async def _confirm(
        self, db: AsyncSession, order_id: str, reservation: _Reservation
    ) -> None:
        confirmed = await db.scalar(
            update(MomoPaymentOrder)
            .where(
                MomoPaymentOrder.order_id == order_id,
                MomoPaymentOrder.granted_at.is_(None),
            )
            .values(granted_at=utc_now())
            .returning(MomoPaymentOrder.id)
            .execution_options(synchronize_session=False)
        )
        if confirmed is not None and reservation.lead_id:
            await mark_lead_claimed(db, reservation.lead_id, reservation.firebase_uid)
        await db.commit()

    async def activate_for_user(
        self, db: AsyncSession, user_id: str, verified_email: str | None
    ) -> None:
        """Grant every paid, ungranted pass this user owns or bought on the web."""
        owned = MomoPaymentOrder.user_id == user_id
        if verified_email:
            bought_on_web = (MomoPaymentOrder.channel == "web_funnel") & (
                MomoPaymentOrder.buyer_email == normalize_email(verified_email)
            )
            owned = owned | bought_on_web
        order_ids = (
            await db.scalars(
                select(MomoPaymentOrder.order_id)
                .where(
                    owned,
                    MomoPaymentOrder.status == "paid",
                    MomoPaymentOrder.granted_at.is_(None),
                )
                .order_by(MomoPaymentOrder.paid_at)
            )
        ).all()
        await db.rollback()
        for order_id in order_ids:
            await self.grant(db, order_id, user_id)

    async def has_active_pass(self, db: AsyncSession, user_id: str) -> bool:
        """Only confirmed grants count; a reserved window may not exist upstream."""
        ends = await db.scalar(
            select(func.max(MomoPaymentOrder.access_ends_at)).where(
                MomoPaymentOrder.granted_user_id == user_id,
                MomoPaymentOrder.granted_at.is_not(None),
            )
        )
        return bool(ends and ends > utc_now())

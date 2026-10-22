"""Turn paid MoMo orders into RevenueCat access windows for a Nutree user.

A grant never calls RevenueCat inside a transaction or under a row lock:

1. Reserve: under the user and order row locks, persist the order's access
   window, stacked after the user's other live windows, and commit.
2. Call RevenueCat with the persisted end time. A retry sends the same end
   time, which RevenueCat treats as a duplicate, so retries never extend access.
3. Confirm: stamp ``granted_at`` once and claim the quiz lead.

An unconfirmed window whose start passed more than the duplicate tolerance ago
has lapsed: resending its end would grant a pass that is partly or wholly over.
The order is confirmed when RevenueCat already holds a promotional ending
there, and is otherwise reserved again from now.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import next_access_window
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_orders import bought_by, mark_lead_claimed

logger = logging.getLogger(__name__)

STANDARD_ENTITLEMENT = "standard"
# RevenueCat treats a grant ending within 2 h of an active promotional's
# expiry as a duplicate of it.
_DUPLICATE_TOLERANCE = timedelta(hours=2)


@dataclass(frozen=True)
class _Reservation:
    firebase_uid: str
    starts_at: datetime
    ends_at: datetime
    lead_id: str | None
    granted: bool

    def lapsed(self) -> bool:
        return not self.granted and self.starts_at < utc_now() - _DUPLICATE_TOLERANCE


class MomoPassEntitlements:
    """Grants are idempotent: an order gets one window and is granted once."""

    def __init__(self, revenuecat):
        self._revenuecat = revenuecat

    async def grant(self, db: AsyncSession, order_id: str, user_id: str) -> bool:
        """Grant one paid order to a user; False leaves it for a later retry."""
        reservation = await self._reserve(db, order_id, user_id)
        if reservation is not None and reservation.lapsed():
            reservation = await self._recover(db, order_id, user_id, reservation)
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

    async def _recover(
        self, db: AsyncSession, order_id: str, user_id: str, lapsed: _Reservation
    ) -> _Reservation | None:
        """Confirm a lapsed window RevenueCat holds, or reserve it again from now."""
        expiries = await self._revenuecat.promotional_expiries(lapsed.firebase_uid)
        if expiries is None:
            return None
        if any(abs(end - lapsed.ends_at) <= _DUPLICATE_TOLERANCE for end in expiries):
            await self._confirm(db, order_id, lapsed)
            logger.info("MoMo pass for order %s was already granted", order_id)
            return replace(lapsed, granted=True)
        logger.error(
            "MoMo pass for order %s stayed ungranted past its start; "
            "reserving it again from now",
            order_id,
        )
        return await self._reserve(
            db,
            order_id,
            user_id,
            replacing=lapsed.ends_at,
            granted_until=max(expiries, default=None),
        )

    async def _reserve(
        self,
        db: AsyncSession,
        order_id: str,
        user_id: str,
        *,
        replacing: datetime | None = None,
        granted_until: datetime | None = None,
    ) -> _Reservation | None:
        """Persist the order's window under row locks; None when it is not paid.

        ``replacing`` names a lapsed window to reserve again, also stacked
        after ``granted_until``; a window another request already replaced is
        kept.
        """
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
        if not order.granted_at and (not reserved or order.access_ends_at == replacing):
            now = utc_now()
            current_end = await self._live_end(db, user_id, order_id, now)
            if granted_until and (not current_end or granted_until > current_end):
                current_end = granted_until
            starts, ends = next_access_window(
                now, current_end, timedelta(weeks=order.weeks)
            )
            order.granted_user_id = user.id
            order.access_starts_at, order.access_ends_at = starts, ends
        reservation = _Reservation(
            firebase_uid=user.firebase_uid,
            starts_at=order.access_starts_at,
            ends_at=order.access_ends_at,
            lead_id=order.lead_id,
            granted=order.granted_at is not None,
        )
        await db.commit()
        return reservation

    @staticmethod
    async def _live_end(
        db: AsyncSession, user_id: str, order_id: str, now: datetime
    ) -> datetime | None:
        """End of the user's other granted or still-resendable windows.

        A lapsed window is left out: its order reserves it again on retry.
        """
        return await db.scalar(
            select(func.max(MomoPaymentOrder.access_ends_at)).where(
                MomoPaymentOrder.granted_user_id == user_id,
                MomoPaymentOrder.order_id != order_id,
                or_(
                    MomoPaymentOrder.granted_at.is_not(None),
                    MomoPaymentOrder.access_starts_at >= now - _DUPLICATE_TOLERANCE,
                ),
            )
        )

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
        order_ids = (
            await db.scalars(
                select(MomoPaymentOrder.order_id)
                .where(
                    bought_by(user_id, verified_email),
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

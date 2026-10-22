"""Turn paid MoMo orders into Premium time on the buyer's RevenueCat account.

Two steps, and no remote call ever runs inside a transaction:

1. Credit (database only): a paid order whose owner is known gets its access
   window once. Under the user row lock it starts now, or when the user's
   latest credited window ends, and lasts the pass's weeks.
2. Sync (RevenueCat): send the end of the user's latest window as a
   promotional ``standard`` entitlement, then mark every credited order that
   ends by then as granted and claim its quiz lead. RevenueCat starts every
   promotional at once and keeps the latest expiry, so resending an end (a
   retry, a lost answer) is a duplicate and an earlier end landing late never
   shortens access: no ordering or recovery is needed.

Accepted trade-off: a window is fixed when it is credited. While RevenueCat is
down the pass still ends on that date, so an outage eats into it; status
polls, IPNs and sign-in retry the sync. When every unsent window has already
ended there is nothing left to deliver: nothing is sent, the orders are marked
granted and an error is logged.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import next_access_window
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_orders import bought_by, lock_order, mark_lead_claimed

logger = logging.getLogger(__name__)

STANDARD_ENTITLEMENT = "standard"

_CREDITED_TO = MomoPaymentOrder.granted_user_id


class MomoPassEntitlements:
    def __init__(self, revenuecat):
        self._revenuecat = revenuecat

    async def credit(self, db: AsyncSession, order_id: str, user_id: str) -> None:
        """Give a paid order its access window; an order is credited only once."""
        # Locked reads overwrite the identity map, or a waiting request would
        # see the window as missing and credit the order again.
        user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
        order = await lock_order(db, order_id)
        if not user or not order or order.status != "paid" or order.access_ends_at:
            await db.rollback()
            return
        latest_end = await db.scalar(
            select(func.max(MomoPaymentOrder.access_ends_at)).where(
                _CREDITED_TO == user.id
            )
        )
        order.granted_user_id = user.id
        order.access_starts_at, order.access_ends_at = next_access_window(
            utc_now(), latest_end, timedelta(weeks=order.weeks)
        )
        await db.commit()

    async def sync(self, db: AsyncSession, user_id: str) -> bool:
        """Send the user's latest pass end to RevenueCat; False leaves a retry."""
        unsent = await _latest_unsent_end(db, user_id)
        # End the read: no transaction stays open across the RevenueCat call.
        await db.rollback()
        if unsent is None:
            return True
        firebase_uid, end = unsent
        if end <= utc_now():
            logger.error(
                "Paid MoMo passes of user %s lapsed before they reached RevenueCat",
                user_id,
            )
        elif not await self._revenuecat.grant_promotional_entitlement(
            firebase_uid, STANDARD_ENTITLEMENT, int(end.timestamp() * 1000)
        ):
            logger.warning("MoMo pass sync deferred for user %s", user_id)
            return False
        await _mark_granted(db, user_id, firebase_uid, end)
        return True

    async def activate_for_user(
        self, db: AsyncSession, user_id: str, verified_email: str | None
    ) -> None:
        """Credit every paid pass this user owns or bought on the web, then sync."""
        order_ids = (
            await db.scalars(
                select(MomoPaymentOrder.order_id)
                .where(
                    bought_by(user_id, verified_email),
                    MomoPaymentOrder.status == "paid",
                    MomoPaymentOrder.access_ends_at.is_(None),
                )
                .order_by(MomoPaymentOrder.paid_at)
            )
        ).all()
        await db.rollback()
        for order_id in order_ids:
            await self.credit(db, order_id, user_id)
        await self.sync(db, user_id)

    async def has_active_pass(self, db: AsyncSession, user_id: str) -> bool:
        """Only granted windows count; a credited one may not exist upstream."""
        ends = await db.scalar(
            select(func.max(MomoPaymentOrder.access_ends_at)).where(
                _CREDITED_TO == user_id, MomoPaymentOrder.granted_at.is_not(None)
            )
        )
        return bool(ends and ends > utc_now())


async def _latest_unsent_end(
    db: AsyncSession, user_id: str
) -> tuple[str, datetime] | None:
    """The user's Firebase uid and latest window end, while one is unsent."""
    row = (
        await db.execute(
            select(User.firebase_uid, func.max(MomoPaymentOrder.access_ends_at))
            .join(MomoPaymentOrder, _CREDITED_TO == User.id)
            .where(User.id == user_id)
            .group_by(User.firebase_uid)
            .having(func.bool_or(MomoPaymentOrder.granted_at.is_(None)))
        )
    ).one_or_none()
    return (row[0], row[1]) if row else None


async def _mark_granted(
    db: AsyncSession, user_id: str, firebase_uid: str, end: datetime
) -> None:
    """Mark the user's unsent windows ending by ``end`` and claim their leads."""
    # The user lock serialises this with the user's credits and other syncs.
    await db.get(User, user_id, with_for_update=True, populate_existing=True)
    lead_ids = (
        await db.scalars(
            update(MomoPaymentOrder)
            .where(
                _CREDITED_TO == user_id,
                MomoPaymentOrder.granted_at.is_(None),
                MomoPaymentOrder.access_ends_at <= end,
            )
            .values(granted_at=utc_now())
            .returning(MomoPaymentOrder.lead_id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    for lead_id in lead_ids:
        if lead_id:
            await mark_lead_claimed(db, lead_id, firebase_uid)
    await db.commit()

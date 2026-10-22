"""Turn paid MoMo orders into RevenueCat access windows for a Nutree user.

RevenueCat promotional grants start when they are made and end at the time
sent, so one grant delivers every window that ends by then. A user's windows
form one chain, and a grant sends the end of the whole chain, never inside a
transaction or under a row lock (the locked steps live in momo_pass_windows):

1. Reserve: persist the order's window, stacked after the user's chain.
2. Call RevenueCat with the chain's end. A retry sends the same end, which
   RevenueCat treats as a duplicate, and the end sent for a user only grows.
3. Confirm every window ending by the end sent, and claim their quiz leads.

When the user holds an unconfirmed window that has lapsed, resending the
chain would grant time that is already over. RevenueCat is read first: the
windows it already covers are confirmed and the rest of the chain is stacked
again from now.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.services import momo_pass_windows as windows
from src.infra.services.momo_pass_orders import bought_by

logger = logging.getLogger(__name__)

STANDARD_ENTITLEMENT = "standard"


class MomoPassEntitlements:
    """Grants are idempotent: each paid week is granted once."""

    def __init__(self, revenuecat):
        self._revenuecat = revenuecat

    async def grant(self, db: AsyncSession, order_id: str, user_id: str) -> bool:
        """Grant one paid order to a user; False leaves it for a later retry."""
        plan = await windows.reserve(db, order_id, user_id)
        if plan is not None and plan.lapsed:
            upstream_ends = await self._revenuecat.promotional_expiries(
                plan.firebase_uid
            )
            # An unreadable subscriber is never mistaken for one without grants.
            if upstream_ends is None:
                return False
            plan = await windows.restack(db, user_id, upstream_ends)
        if plan is None:
            return False
        if plan.send_end is None:
            return True
        granted = await self._revenuecat.grant_promotional_entitlement(
            plan.firebase_uid,
            STANDARD_ENTITLEMENT,
            int(plan.send_end.timestamp() * 1000),
        )
        if not granted:
            logger.warning("MoMo pass grant deferred for order %s", order_id)
            return False
        await windows.confirm_through(db, user_id, plan.firebase_uid, plan.send_end)
        logger.info("MoMo pass granted for order %s", order_id)
        return True

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

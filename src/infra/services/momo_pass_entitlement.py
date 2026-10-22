"""Turn paid MoMo orders into RevenueCat access windows for a Nutree user."""

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import next_access_window
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_orders import mark_lead_claimed, normalize_email

logger = logging.getLogger(__name__)

STANDARD_ENTITLEMENT = "standard"


class MomoPassEntitlements:
    """Grants are idempotent: an order is granted once, under the user's row lock."""

    def __init__(self, revenuecat):
        self._revenuecat = revenuecat

    async def grant(self, db: AsyncSession, order_id: str, user_id: str) -> bool:
        """Grant one paid order to a user, stacking after any pass they still hold."""
        user = await db.get(User, user_id, with_for_update=True)
        order = await db.scalar(
            select(MomoPaymentOrder)
            .where(MomoPaymentOrder.order_id == order_id)
            .with_for_update()
        )
        if not user or not order or order.status != "paid":
            await db.rollback()
            return False
        if order.granted_at:
            await db.rollback()
            return True
        current_end = await db.scalar(
            select(func.max(MomoPaymentOrder.access_ends_at)).where(
                MomoPaymentOrder.granted_user_id == user.id,
                MomoPaymentOrder.granted_at.is_not(None),
            )
        )
        starts, ends = next_access_window(
            utc_now(), current_end, timedelta(weeks=order.weeks)
        )
        granted = await self._revenuecat.grant_promotional_entitlement(
            user.firebase_uid, STANDARD_ENTITLEMENT, int(ends.timestamp() * 1000)
        )
        if not granted:
            await db.rollback()
            logger.warning("MoMo pass grant deferred for order %s", order.order_id)
            return False
        order.granted_user_id = user.id
        order.access_starts_at, order.access_ends_at = starts, ends
        order.granted_at = utc_now()
        if order.lead_id:
            await mark_lead_claimed(db, order.lead_id, user.firebase_uid)
        await db.commit()
        logger.info("MoMo pass granted for order %s", order.order_id)
        return True

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
        ends = await db.scalar(
            select(func.max(MomoPaymentOrder.access_ends_at)).where(
                MomoPaymentOrder.granted_user_id == user_id
            )
        )
        return bool(ends and ends > utc_now())

"""Locked database steps of a MoMo pass grant; nothing here calls RevenueCat.

Every step that changes a window first locks the user row, so one user's
windows change one request at a time, and commits before it returns.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import next_access_window
from src.domain.model.payments.momo_pass_chain import (
    DUPLICATE_TOLERANCE,
    PassWindow,
    recover_chain,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_orders import lock_order, mark_lead_claimed

logger = logging.getLogger(__name__)

_OWNED_BY = MomoPaymentOrder.granted_user_id


@dataclass(frozen=True)
class GrantPlan:
    firebase_uid: str
    send_end: datetime | None
    """End of the user's whole chain, or None when nothing is left to grant."""
    lapsed: bool = False
    """The user holds an unconfirmed window that has lapsed."""


async def reserve(db: AsyncSession, order_id: str, user_id: str) -> GrantPlan | None:
    """Persist the order's window after the user's chain; None when it is unpaid."""
    # Locked reads overwrite the identity map: a stale granted_at or window
    # would let a waiting request grant the order again.
    user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    order = await lock_order(db, order_id)
    if not user or not order or order.status != "paid":
        await db.rollback()
        return None
    if order.granted_at:
        await db.commit()
        return GrantPlan(user.firebase_uid, send_end=None)
    now = utc_now()
    # Lapsed windows stay in the chain until their recovery moves them, so a
    # window whose grant landed upstream is never overlapped.
    chain_end, lapsed = (
        await db.execute(
            select(
                func.max(MomoPaymentOrder.access_ends_at),
                func.bool_or(
                    and_(
                        MomoPaymentOrder.granted_at.is_(None),
                        MomoPaymentOrder.access_starts_at < now - DUPLICATE_TOLERANCE,
                    )
                ),
            ).where(_OWNED_BY == user.id)
        )
    ).one()
    if order.granted_user_id != user.id or not order.access_ends_at:
        order.granted_user_id = user.id
        order.access_starts_at, order.access_ends_at = next_access_window(
            now, chain_end, timedelta(weeks=order.weeks)
        )
    plan = GrantPlan(
        user.firebase_uid,
        send_end=max(order.access_ends_at, chain_end or order.access_ends_at),
        lapsed=bool(lapsed),
    )
    await db.commit()
    return plan


async def restack(
    db: AsyncSession, user_id: str, upstream_ends: list[datetime]
) -> GrantPlan | None:
    """Settle the user's lapsed chain against RevenueCat's promotional ends."""
    user = await db.get(User, user_id, with_for_update=True, populate_existing=True)
    if not user:
        await db.rollback()
        return None
    unconfirmed = (
        await db.scalars(
            select(MomoPaymentOrder)
            .where(
                _OWNED_BY == user.id,
                MomoPaymentOrder.granted_at.is_(None),
                MomoPaymentOrder.access_ends_at.is_not(None),
            )
            .order_by(MomoPaymentOrder.access_starts_at, MomoPaymentOrder.paid_at)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    confirmed_end = await db.scalar(
        select(func.max(MomoPaymentOrder.access_ends_at)).where(
            _OWNED_BY == user.id, MomoPaymentOrder.granted_at.is_not(None)
        )
    )
    now = utc_now()
    recovery = recover_chain(
        [_window(order) for order in unconfirmed], confirmed_end, upstream_ends, now
    )
    orders = {order.order_id: order for order in unconfirmed}
    for order_id in sorted(recovery.delivered):
        orders[order_id].granted_at = now
        if lead_id := orders[order_id].lead_id:
            await mark_lead_claimed(db, lead_id, user.firebase_uid)
    for window in recovery.moved:
        order = orders[window.order_id]
        order.access_starts_at, order.access_ends_at = window.starts_at, window.ends_at
    if recovery.delivered:
        logger.info("MoMo passes %s were already granted", sorted(recovery.delivered))
    if recovery.moved:
        logger.error(
            "MoMo passes %s stayed ungranted past their start; stacking them "
            "again from now",
            [window.order_id for window in recovery.moved],
        )
    # Every window still pending ends after what RevenueCat or a confirmed
    # grant already covers, so the chain's end is the latest pending end.
    pending = [order.access_ends_at for order in unconfirmed if not order.granted_at]
    await db.commit()
    return GrantPlan(user.firebase_uid, send_end=max(pending, default=None))


async def confirm_through(
    db: AsyncSession, user_id: str, firebase_uid: str, end: datetime
) -> None:
    """Confirm every unconfirmed window of the user that a grant to ``end`` covers."""
    # The user lock orders this after a concurrent restack, so a window it
    # moved past ``end`` is not confirmed, and the row locks never deadlock.
    await db.get(User, user_id, with_for_update=True, populate_existing=True)
    lead_ids = (
        await db.scalars(
            update(MomoPaymentOrder)
            .where(
                _OWNED_BY == user_id,
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


def _window(order: MomoPaymentOrder) -> PassWindow:
    return PassWindow(
        order_id=order.order_id,
        starts_at=order.access_starts_at,
        ends_at=order.access_ends_at,
        duration=timedelta(weeks=order.weeks),
    )

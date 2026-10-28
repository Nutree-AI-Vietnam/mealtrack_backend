"""Turn paid MoMo orders into Premium time on the buyer's RevenueCat account.

1. Credit (database only): a paid order whose owner is known gets its access
   window once. Under the user and order row locks it starts now, or when the
   user's latest credited window ends, and lasts the pass's weeks.
2. Sync (RevenueCat): send the end of the user's latest window as a
   promotional ``standard`` entitlement, then mark every credited order that
   ends by then as granted and claim its quiz lead.

RevenueCat may replace a custom promotional instead of keeping the latest
expiry, so one user's sends must land in order: only the holder of the user's
transaction-scoped advisory lock sends, always the latest credited end, until
nothing is unsent. A status read or IPN that finds the lock taken returns, as
the holder's next pass sends its committed credit; activation waits a little
longer than one grant, so the pass it reports has reached RevenueCat. A failed
grant leaves the orders unsent for the next status read, IPN or activation.

Accepted trade-off: the holder keeps a connection, in a read transaction with
the advisory lock and no row locks, for up to ``_GRANT_TIMEOUT_SECONDS`` while
RevenueCat answers, as does an activation waiting behind it. No other
transaction stays open across a remote call; crediting never waits on it.

Residual risk: a grant that timed out here may still land at RevenueCat after
a later end was sent, and shorten access to its earlier end; nothing detects
that. A window is also fixed when it is credited, so while RevenueCat is down
the pass still ends on that date, and a window that ended before delivery is
not sent (it is marked and logged as an error).
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import next_access_window
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services.momo_pass_orders import bought_by, lock_order, mark_lead_claimed

logger = logging.getLogger(__name__)

STANDARD_ENTITLEMENT = "standard"

_GRANT_TIMEOUT_SECONDS = 10.0
# Long enough for the holder to finish one grant and release the lock.
_LOCK_WAIT_SECONDS = _GRANT_TIMEOUT_SECONDS + 2.0
_LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE of an expired lock_timeout

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

    async def sync(self, db: AsyncSession, user_id: str, *, wait: bool = False) -> bool:
        """Send the user's latest pass end to RevenueCat; False leaves a retry.

        True also when another request holds the user's sync lock: that holder
        sends the latest end, this request's credit included. ``wait`` queues
        behind the holder, so its grant has landed or failed on return. Call
        with no transaction open, so the lock's transaction has no row locks.
        """
        while await _latest_unsent_end(db, user_id):
            if not await _sync_lock(db, user_id, wait):
                break
            # Re-read under the lock: the previous holder may have sent it,
            # and only the end read while holding the lock may be sent.
            if unsent := await _latest_unsent_end(db, user_id):
                firebase_uid, end = unsent
                if end <= utc_now():
                    logger.error(
                        "Paid MoMo passes of user %s lapsed before they "
                        "reached RevenueCat",
                        user_id,
                    )
                elif not await self._grant(user_id, firebase_uid, end):
                    await db.rollback()
                    return False
                await _mark_granted(db, user_id, firebase_uid, end)
            # Releases the lock; the next pass picks up credits made meanwhile.
            await db.commit()
        await db.rollback()
        return True

    async def _grant(self, user_id: str, firebase_uid: str, end: datetime) -> bool:
        try:
            async with asyncio.timeout(_GRANT_TIMEOUT_SECONDS):
                granted = await self._revenuecat.grant_promotional_entitlement(
                    firebase_uid, STANDARD_ENTITLEMENT, int(end.timestamp() * 1000)
                )
        except TimeoutError:
            granted = False
        if not granted:
            logger.warning("MoMo pass sync deferred for user %s", user_id)
        return granted

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
        await self.sync(db, user_id, wait=True)

    async def has_active_pass(self, db: AsyncSession, user_id: str) -> bool:
        """Only granted windows count; a credited one may not exist upstream."""
        ends = await db.scalar(
            select(func.max(MomoPaymentOrder.access_ends_at)).where(
                _CREDITED_TO == user_id, MomoPaymentOrder.granted_at.is_not(None)
            )
        )
        return bool(ends and ends > utc_now())


async def _sync_lock(db: AsyncSession, user_id: str, wait: bool) -> bool:
    """Take the user's sync lock until this transaction ends.

    Without ``wait`` a taken lock answers False at once. With it the request
    queues for up to ``_LOCK_WAIT_SECONDS``; a wait that runs out rolls back.
    """
    digest = hashlib.sha256(f"momo-sync:{user_id}".encode()).digest()
    key = {"key": int.from_bytes(digest[:8], "big", signed=True)}
    if not wait:
        return bool(
            await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), key)
        )
    timeout = {"timeout": f"{int(_LOCK_WAIT_SECONDS * 1000)}ms"}
    await db.execute(text("SELECT set_config('lock_timeout', :timeout, true)"), timeout)
    try:
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), key)
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
            raise
        await db.rollback()
        return False
    return True


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
    """Mark the user's unsent windows ending by ``end`` and claim their leads.

    The sync lock orders this with other syncs; a window being credited
    meanwhile has no end yet, so it is left for the next pass.
    """
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

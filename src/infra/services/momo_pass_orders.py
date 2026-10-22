"""Shared reads and projections for MoMo one-time pass orders."""

from datetime import timedelta
from hmac import compare_digest

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import MomoPassPlan
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.web_funnel_claim import WebFunnelLead

CLOSED_LEAD_STATUSES = frozenset({"revoked", "refunded", "conflict"})


async def find_order(db: AsyncSession, order_id: str) -> MomoPaymentOrder | None:
    return await db.scalar(
        select(MomoPaymentOrder)
        .where(MomoPaymentOrder.order_id == order_id)
        .execution_options(populate_existing=True)
    )


async def lock_order(db: AsyncSession, order_id: str) -> MomoPaymentOrder | None:
    # Sessions keep objects across commits, so a locked read must overwrite the
    # identity map or it returns what this session saw before the lock.
    return await db.scalar(
        select(MomoPaymentOrder)
        .where(MomoPaymentOrder.order_id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def claim_query_slot(
    db: AsyncSession, order_id: str, interval: timedelta
) -> bool:
    """Stamp a pending order's query time; only the caller that stamps asks MoMo.

    Commits, so the stamp holds even when the MoMo call that follows fails.
    """
    now = utc_now()
    claimed = await db.scalar(
        update(MomoPaymentOrder)
        .where(
            MomoPaymentOrder.order_id == order_id,
            MomoPaymentOrder.status == "pending",
            or_(
                MomoPaymentOrder.last_checked_at.is_(None),
                MomoPaymentOrder.last_checked_at < now - interval,
            ),
        )
        .values(last_checked_at=now)
        .returning(MomoPaymentOrder.id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return claimed is not None


async def owned_lead(
    db: AsyncSession, lead_id: str | None, access_key_hash: str
) -> WebFunnelLead | None:
    """The lead only when this browser holds its access key."""
    lead = await db.get(WebFunnelLead, lead_id) if lead_id else None
    if not lead or not compare_digest(lead.access_key_hash, access_key_hash):
        return None
    return lead


async def has_paid_before(
    db: AsyncSession, user_id: str | None, email: str | None
) -> bool:
    owners = []
    if user_id:
        owners += [
            MomoPaymentOrder.user_id == user_id,
            MomoPaymentOrder.granted_user_id == user_id,
        ]
    if email:
        owners.append(MomoPaymentOrder.buyer_email == normalize_email(email))
    if not owners:
        return False
    found = await db.scalar(
        select(MomoPaymentOrder.id)
        .where(MomoPaymentOrder.status == "paid", or_(*owners))
        .limit(1)
    )
    return found is not None


async def mark_lead_checkout_started(db: AsyncSession, lead_id: str) -> None:
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if lead and lead.status == "draft":
        lead.status = "checkout_started"


async def mark_lead_paid(db: AsyncSession, lead_id: str) -> None:
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if lead and lead.status in {"draft", "checkout_started", "expired"}:
        lead.status, lead.payment_verified_at = "payment_verified", utc_now()
        lead.access_sync_status = "pending"


async def mark_lead_claimed(db: AsyncSession, lead_id: str, uid: str) -> None:
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if not lead or lead.status in {"claimed", "refunded", "revoked"}:
        return
    lead.status, lead.claimed_at, lead.claimed_uid = "claimed", utc_now(), uid
    lead.access_sync_status = "active"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def order_projection(order: MomoPaymentOrder) -> dict:
    return {
        "order_id": order.order_id,
        "status": order.status,
        "plan": order.plan,
        "amount": order.amount,
        "access_ends_at": order.access_ends_at.isoformat()
        if order.access_ends_at
        else None,
    }


def plan_projection(plan: MomoPassPlan, paid_before: bool) -> dict:
    return {
        "plan": plan.key.value,
        "weeks": plan.weeks,
        "amount": plan.amount_for(paid_before),
        "regular_amount": plan.regular_amount,
        "intro": not paid_before,
    }


def order_info(plan: MomoPassPlan, lang: str) -> str:
    unit = "weeks" if lang == "en" else "tuần"
    return f"Nutree Premium {plan.weeks} {unit}"


def as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None

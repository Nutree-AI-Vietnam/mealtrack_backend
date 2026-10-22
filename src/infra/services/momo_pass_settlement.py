"""Settle MoMo orders from signed IPNs and from MoMo's query API."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import order_status_for_result
from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.momo_gateway_adapter import MomoGatewayAdapter, MomoGatewayError
from src.infra.services import momo_pass_orders as orders

logger = logging.getLogger(__name__)

# MoMo is asked about a pending order at most this often.
QUERY_INTERVAL = timedelta(seconds=3)


async def settle_pending(
    db: AsyncSession,
    gateway: MomoGatewayAdapter,
    order_id: str,
    interval: timedelta = QUERY_INTERVAL,
) -> None:
    """Ask MoMo about a pending order and apply its answer.

    Only the caller that claims the order's query slot asks MoMo, so concurrent
    reads and a failing MoMo cost at most one query per interval. Call with no
    transaction open: none stays open across the MoMo call.
    """
    if gateway.credentials.missing or not await orders.claim_query_slot(
        db, order_id, interval
    ):
        return
    try:
        result = await gateway.query_payment(
            order_id=order_id, request_id=uuid.uuid4().hex
        )
    except MomoGatewayError as error:
        logger.warning("MoMo query failed for %s: %s", order_id, error)
        return
    await apply_result(db, order_id, result)


async def apply_result(db: AsyncSession, order_id: str, result: dict) -> None:
    """Record a trusted MoMo result on the order; paid is terminal."""
    order = await orders.lock_order(db, order_id)
    result_code = result.get("resultCode")
    if not order or not isinstance(result_code, int):
        await db.rollback()
        return
    status = order_status_for_result(result_code).value
    # Paid is terminal; a late paid result may still replace failed/expired.
    if order.status == "paid" or status == "pending":
        await db.rollback()
        return
    if status == "paid" and orders.as_int(result.get("amount")) != order.amount:
        logger.error("MoMo amount mismatch on order %s", order_id)
        await db.rollback()
        return
    order.status, order.result_code = status, result_code
    order.result_message = str(result.get("message") or "")[:255]
    if status == "paid":
        order.trans_id = orders.as_int(result.get("transId"))
        order.pay_type = str(result.get("payType") or "")[:32] or None
        order.paid_at = utc_now()
        if order.lead_id:
            await orders.mark_lead_paid(db, order.lead_id)
    await db.commit()

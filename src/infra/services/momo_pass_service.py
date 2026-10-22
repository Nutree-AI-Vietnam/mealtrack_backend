"""MoMo one-time pass orders: checkout, IPN and query reconciliation.

Each public method opens its own database session so API routes stay free of
infrastructure imports.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.payments.momo_pass import (
    MOMO_PASS_PLANS,
    MomoPassPlan,
    MomoPayMethod,
)
from src.domain.model.payments.momo_signature import is_valid_result_signature
from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.momo_gateway_adapter import MomoGatewayAdapter, MomoGatewayError
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.services import momo_pass_orders as orders
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_settlement import apply_result, settle_pending

logger = logging.getLogger(__name__)

# Activation asks MoMo about the buyer's newest pending orders from this
# period, so a payment whose IPN and status polls were all missed still unlocks.
_ACTIVATE_SETTLE_PERIOD = timedelta(days=30)
_ACTIVATE_SETTLE_LIMIT = 3


class MomoNotConfigured(Exception):
    def __init__(self, missing: list[str]):
        super().__init__(f"MoMo is not configured: {', '.join(missing)}")
        self.missing = missing


class MomoLeadUnavailable(Exception):
    """The quiz lead is unknown, not this browser's, or closed."""


@dataclass(frozen=True)
class MomoUrls:
    ipn_url: str
    web_redirect_url: str
    app_redirect_url: str


@dataclass(frozen=True)
class MomoBuyer:
    channel: str
    user_id: str | None = None
    lead_id: str | None = None
    email: str | None = None


class MomoPassService:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        gateway: MomoGatewayAdapter,
        entitlements: MomoPassEntitlements,
        urls: MomoUrls,
    ):
        self._session = session_factory
        self.gateway = gateway
        self.entitlements = entitlements
        self.urls = urls

    async def web_buyer(self, lead_id: str, access_key_hash: str) -> MomoBuyer:
        async with self._session() as db:
            lead = await orders.owned_lead(db, lead_id, access_key_hash)
            if not lead or lead.status in orders.CLOSED_LEAD_STATUSES:
                raise MomoLeadUnavailable()
            return MomoBuyer(channel="web_funnel", lead_id=lead.id, email=lead.email)

    async def plans_for(self, buyer: MomoBuyer) -> list[dict]:
        async with self._session() as db:
            paid_before = await _paid_before(db, buyer)
        return [
            orders.plan_projection(plan, paid_before)
            for plan in MOMO_PASS_PLANS.values()
        ]

    async def start_checkout(
        self,
        buyer: MomoBuyer,
        plan: MomoPassPlan,
        *,
        lang: str,
        method: MomoPayMethod = MomoPayMethod.WALLET,
    ) -> dict:
        in_app = buyer.channel == "app"
        redirect_url = (
            self.urls.app_redirect_url if in_app else self.urls.web_redirect_url
        )
        missing = list(self.gateway.credentials.missing)
        missing += [
            name
            for name, value in (
                ("MOMO_IPN_URL", self.urls.ipn_url),
                ("MOMO_REDIRECT_URL", redirect_url),
            )
            if not value
        ]
        if missing:
            raise MomoNotConfigured(missing)
        async with self._session() as db:
            paid_before = await _paid_before(db, buyer)
            order = MomoPaymentOrder(
                order_id=uuid.uuid4().hex,
                request_id=uuid.uuid4().hex,
                channel=buyer.channel,
                request_type=method.request_type,
                plan=plan.key.value,
                weeks=plan.weeks,
                amount=plan.amount_for(paid_before),
                intro_price=not paid_before,
                user_id=buyer.user_id,
                lead_id=buyer.lead_id,
                buyer_email=orders.normalize_email(buyer.email)
                if buyer.email
                else None,
                status="pending",
            )
            db.add(order)
            if buyer.lead_id:
                await orders.mark_lead_checkout_started(db, buyer.lead_id)
            # Commit first so an IPN racing the create response finds the order.
            await db.commit()
            try:
                created = await self.gateway.create_payment(
                    order_id=order.order_id,
                    request_id=order.request_id,
                    amount=order.amount,
                    order_info=orders.order_info(plan, lang),
                    redirect_url=redirect_url,
                    ipn_url=self.urls.ipn_url,
                    request_type=order.request_type,
                    lang=lang,
                )
            except MomoGatewayError as error:
                order.status, order.result_message = "failed", str(error)[:255]
                await db.commit()
                raise
            return {
                "order_id": order.order_id,
                "pay_url": created["payUrl"],
                "deeplink": created.get("deeplink"),
                "amount": order.amount,
            }

    async def app_order(self, order_id: str, user_id: str) -> dict | None:
        async with self._session() as db:
            order = await orders.find_order(db, order_id)
            if not order or order.user_id != user_id:
                return None
            return await self._refresh(db, order_id)

    async def web_order(self, order_id: str, access_key_hash: str) -> dict | None:
        async with self._session() as db:
            order = await orders.find_order(db, order_id)
            if not order or not await orders.owned_lead(
                db, order.lead_id, access_key_hash
            ):
                return None
            return await self._refresh(db, order_id)

    async def handle_ipn(self, payload: dict) -> bool:
        """Apply a signed MoMo notification. False means the body is not trusted."""
        credentials = self.gateway.credentials
        # Unconfigured MoMo has empty keys, and an empty key must never verify.
        if credentials.missing:
            return False
        if payload.get("partnerCode") != credentials.partner_code or not (
            is_valid_result_signature(
                credentials.secret_key, credentials.access_key, payload
            )
        ):
            return False
        order_id = str(payload.get("orderId"))
        async with self._session() as db:
            if not await orders.find_order(db, order_id):
                return False
            await apply_result(db, order_id, payload)
            await self._deliver_if_paid(db, order_id)
        return True

    async def activate(self, user_id: str, verified_email: str | None) -> bool:
        """Settle, credit and sync a signed-in user's passes; True when one is active.

        Runs on sign-in and when the app activates without Premium. The IPN and
        the buyer's status polls can all be missed (a deploy, or MoMo handing
        back to another browser), so this first asks MoMo about the buyer's few
        newest pending orders. It skips the status-poll throttle: a poll holding
        the query slot cannot credit a quiz order that has no owner yet, so
        activation must get its own answer. It is bounded by the order limit and
        the route's rate limit, and applying a result is idempotent.
        """
        if not self.gateway.credentials.missing:
            async with self._session() as db:
                pending = await orders.recent_pending_order_ids(
                    db,
                    user_id,
                    verified_email,
                    since=utc_now() - _ACTIVATE_SETTLE_PERIOD,
                    limit=_ACTIVATE_SETTLE_LIMIT,
                )
            # One session per order, so activation waits for one MoMo answer.
            await asyncio.gather(
                *(self._settle_alone(order_id) for order_id in pending)
            )
        async with self._session() as db:
            await self.entitlements.activate_for_user(db, user_id, verified_email)
            return await self.entitlements.has_active_pass(db, user_id)

    async def _settle_alone(self, order_id: str) -> None:
        """A failed settle leaves the order pending and never blocks activation."""
        try:
            async with self._session() as db:
                await settle_pending(db, self.gateway, order_id, interval=timedelta(0))
        except Exception:
            logger.exception("MoMo order %s could not be settled", order_id)

    async def _refresh(self, db: AsyncSession, order_id: str) -> dict | None:
        """Ask MoMo about a pending order, then retry an undelivered pass.

        No transaction stays open across the MoMo call; see the entitlements
        for the one held across the RevenueCat call.
        """
        await db.rollback()
        await settle_pending(db, self.gateway, order_id)
        await self._deliver_if_paid(db, order_id)
        order = await orders.find_order(db, order_id)
        return orders.order_projection(order) if order else None

    async def _deliver_if_paid(self, db: AsyncSession, order_id: str) -> None:
        """Credit and sync a paid order once its owner is known.

        In-app orders belong to their buyer; quiz orders wait for the sign-in
        or app activate that credits them, after which their status poll and
        IPN retry too.
        """
        order = await orders.find_order(db, order_id)
        if not order or order.status != "paid" or order.granted_at:
            return
        if owner := order.granted_user_id or order.user_id:
            await self.entitlements.credit(db, order_id, owner)
            await self.entitlements.sync(db, owner)


async def _paid_before(db: AsyncSession, buyer: MomoBuyer) -> bool:
    """Whether the buyer pays the regular price instead of the intro price."""
    if buyer.channel == "web_funnel":
        # Only the lead's own orders: an anonymous quiz visitor must not learn
        # whether an email they typed has bought before.
        return await orders.has_paid_before(db, lead_id=buyer.lead_id)
    return await orders.has_paid_before(db, user_id=buyer.user_id, email=buyer.email)

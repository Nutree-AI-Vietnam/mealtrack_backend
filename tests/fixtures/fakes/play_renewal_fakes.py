"""Play plans, Play purchases and a fake Play adapter for the lifetime webhook tests."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    SUBSCRIBER,
    USER,
    commit,
    event,
    lifetime,
    ms,
)

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

PLAY_YEARLY = "nutree_annual_599k:yearly"
PLAY_MONTHLY = "nutree_monthly_149k:monthly"
# RevenueCat's transaction id for a Play purchase is its Play order id.
YEARLY_ORDER = "GPA.3372-1111-2222-33333"
MONTHLY_ORDER = "GPA.3372-4444-5555-66666..2"
LIFETIME_ORDER = "GPA.3372-7777-8888-99999"


def play_yearly_purchase(**fields: Any) -> dict[str, Any]:
    fields.setdefault("expiration_at_ms", ms(365))
    fields.setdefault("transaction_id", YEARLY_ORDER)
    return event("INITIAL_PURCHASE", PLAY_YEARLY, store="PLAY_STORE", **fields)


def play_lifetime(product: str = LIFETIME_PRODUCT_ID, **fields: Any) -> dict[str, Any]:
    fields.setdefault("transaction_id", LIFETIME_ORDER)
    return lifetime(product, store="PLAY_STORE", **fields)


def add_plan_row(
    uow: SimpleNamespace,
    product: str = PLAY_YEARLY,
    order: str | None = YEARLY_ORDER,
    **fields: Any,
) -> Subscription:
    """A committed Play plan row of the test user, renewing a year from now."""
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "user_id": USER.id,
        "revenuecat_subscriber_id": SUBSCRIBER,
        "product_id": product,
        "platform": "android",
        "status": "active",
        "purchased_at": utc_now() - timedelta(minutes=5),
        "expires_at": utc_now() + timedelta(days=365),
        "store_transaction_id": order,
        "is_sandbox": False,
        **fields,
    }
    row = Subscription(**values)
    uow.session.add(row)
    commit(uow)
    return row


def fake_play_service(**fields: Any) -> SimpleNamespace:
    """An enabled adapter whose cancel succeeds unless a test says otherwise."""
    values: dict[str, Any] = {
        "enabled": True,
        "sandbox_enabled": False,
        "cancel_renewal": AsyncMock(),
        **fields,
    }
    return SimpleNamespace(**values)

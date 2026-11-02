"""Play plans, Play purchases and a fake Play adapter for the lifetime webhook tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    SUBSCRIBER,
    USER,
    commit,
    event,
    lifetime,
    ms,
)

from src.api.routes.v1.webhooks import revenuecat_webhook
from src.domain.model.streak import StreakRestorePolicy
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

ROUTE = "src.api.routes.v1.webhooks"

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


@contextmanager
def webhook_route(
    uow: SimpleNamespace, publisher: Any, seen: list[Any] | None = None
) -> Iterator[None]:
    """The RevenueCat route over the SQLite unit of work.

    Each unit of work commits on a clean exit and appends "commit" to ``seen``.
    """
    log = seen if seen is not None else []

    def unit_of_work() -> MagicMock:
        unit = MagicMock()
        unit.__aenter__ = AsyncMock(return_value=uow)

        async def exit_(exc_type: Any, exc: Any, tb: Any) -> bool:
            if exc_type is None:
                commit(uow)
                log.append("commit")
            return False

        unit.__aexit__ = AsyncMock(side_effect=exit_)
        return unit

    with ExitStack() as stack:

        def mock(name: str, **kwargs: Any) -> Any:
            return stack.enter_context(patch(f"{ROUTE}.{name}", **kwargs))

        mock("os.getenv", return_value="test_secret")
        mock("AsyncUnitOfWork", side_effect=unit_of_work)
        mock("record_web_funnel_redemption", new_callable=AsyncMock, return_value=False)
        mock(
            "find_user_for_revenuecat_event", new_callable=AsyncMock, return_value=USER
        )
        mock("_get_affiliate_handler", return_value=None)
        mock("_get_event_publisher", return_value=publisher)
        mock("get_streak_restore_policy", return_value=StreakRestorePolicy())
        mock("get_cache_service", return_value=None)
        mock("purge_streak_cache", new_callable=AsyncMock)
        yield


async def post_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    request = MagicMock(json=AsyncMock(return_value={"event": payload}))
    return await revenuecat_webhook(request, authorization="test_secret")

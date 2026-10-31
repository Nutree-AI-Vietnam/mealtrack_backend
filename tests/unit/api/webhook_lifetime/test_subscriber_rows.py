"""The per-subscriber lock, and which plan row a lifetime purchase may convert."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
    MONTHLY,
    SUBSCRIBER,
    USER,
    commit,
    event,
    lifetime,
    ms,
    rows,
    yearly,
)

from src.api.routes.v1.webhook_lifetime_purchase import (
    handle_lifetime_purchase,
    handle_lifetime_refund,
)
from src.api.routes.v1.webhook_referral_funnel import handle_transfer
from src.api.routes.v1.webhook_subscription_rows import lock_subscriber
from src.api.routes.v1.webhooks import (
    handle_billing_issue,
    handle_cancellation,
    handle_purchase,
    handle_renewal,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

pytestmark = pytest.mark.asyncio

ROUTES = "src.api.routes.v1"
ROWS_MODULE = "webhook_subscription_rows"


def _session(dialect: str) -> MagicMock:
    session = MagicMock(execute=AsyncMock())
    session.get_bind.return_value = SimpleNamespace(
        dialect=SimpleNamespace(name=dialect)
    )
    return session


async def test_the_lock_is_a_postgres_advisory_lock_on_the_subscriber():
    session = _session("postgresql")

    await lock_subscriber(SimpleNamespace(session=session), SUBSCRIBER)

    statement, params = session.execute.await_args.args
    assert "pg_advisory_xact_lock(hashtext(:subscriber_id))" in str(statement)
    assert params == {"subscriber_id": SUBSCRIBER}


@pytest.mark.parametrize(
    ("dialect", "subscriber"), [("sqlite", SUBSCRIBER), ("postgresql", None)]
)
async def test_the_lock_is_skipped_without_postgres_or_a_subscriber(
    dialect, subscriber
):
    session = _session(dialect)

    await lock_subscriber(SimpleNamespace(session=session), subscriber)

    session.execute.assert_not_awaited()


def _record_locks_and_reads(uow, module: str):
    """Patch the module's lock and the repository so both log in one list."""
    order: list[str] = []
    find_all = uow.subscriptions.find_all_by_revenuecat_id

    async def record_find(subscriber_id):
        order.append(f"read:{subscriber_id}")
        return await find_all(subscriber_id)

    async def record_lock(_uow, subscriber_id):
        order.append(f"lock:{subscriber_id}")

    uow.subscriptions.find_all_by_revenuecat_id = record_find
    return order, patch(f"{ROUTES}.{module}.lock_subscriber", side_effect=record_lock)


@pytest.mark.parametrize(
    ("handler", "payload", "module"),
    [
        (handle_purchase, yearly("INITIAL_PURCHASE"), ROWS_MODULE),
        (handle_renewal, yearly("RENEWAL"), ROWS_MODULE),
        # A quick "turn off renewal" must wait for an upgrade on the same row.
        (handle_cancellation, yearly("CANCELLATION"), ROWS_MODULE),
        (handle_billing_issue, yearly("BILLING_ISSUE"), ROWS_MODULE),
        (handle_lifetime_purchase, lifetime(), "webhook_lifetime_purchase"),
        (
            handle_lifetime_refund,
            event("CANCELLATION", LIFETIME_PRODUCT_ID),
            "webhook_lifetime_purchase",
        ),
    ],
)
async def test_handlers_lock_the_subscriber_before_reading_its_rows(
    uow, publisher, handler, payload, module
):
    order, lock = _record_locks_and_reads(uow, module)
    with lock:
        await handler(uow, USER, payload, event_publisher=publisher)

    assert order[:2] == [f"lock:{SUBSCRIBER}", f"read:{SUBSCRIBER}"]


async def test_a_transfer_locks_both_accounts_in_one_order_before_reading(uow):
    order, lock = _record_locks_and_reads(uow, "webhook_referral_funnel")
    payload = {
        "type": "TRANSFER",
        "transferred_from": ["uid-z", "uid-a"],
        "transferred_to": [SUBSCRIBER],
    }
    with lock:
        await handle_transfer(uow, payload)

    assert order[:3] == ["lock:firebase-uid-1", "lock:uid-a", "lock:uid-z"]
    assert all(entry.startswith("read:") for entry in order[3:])


async def test_lifetime_bought_outright_gets_its_own_row_beside_a_running_plan(
    uow, deliver
):
    await deliver(
        handle_purchase, event("INITIAL_PURCHASE", MONTHLY, expiration_at_ms=ms(30))
    )

    await deliver(handle_lifetime_purchase, lifetime())

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "active", LIFETIME_PRODUCT_ID: "active"}


async def test_the_upgrade_converts_the_yearly_row_not_another_plan(uow, deliver):
    """A web-funnel monthly row, bought later, sits beside the yearly row."""
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE", purchased_at_ms=ms(-2)))
    uow.session.add(
        Subscription(
            id=str(uuid.uuid4()),
            user_id=USER.id,
            revenuecat_subscriber_id=SUBSCRIBER,
            product_id=MONTHLY,
            platform="web",
            status="active",
            purchased_at=utc_now(),
            expires_at=utc_now() + timedelta(days=30),
        )
    )
    commit(uow)

    await deliver(handle_lifetime_purchase, lifetime(LIFETIME_UPGRADE_PRODUCT_ID))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "active", LIFETIME_UPGRADE_PRODUCT_ID: "active"}

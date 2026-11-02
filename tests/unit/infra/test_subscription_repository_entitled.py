"""The entitled-subscription query keeps paid-up rows and drops expired or sandbox ones."""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql
from tests.fixtures.fakes.lifetime_webhook_db import commit, open_subscription_uow

from src.domain.constants.lifetime_products import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_PRODUCT_IDS,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription
from src.infra.repositories.subscription_repository_async import (
    ENTITLED_SUBSCRIPTION_STATUSES,
    AsyncSubscriptionRepository,
)


async def _compiled_query(*, exclude_sandbox: bool):
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    session.execute = AsyncMock(return_value=result)
    await AsyncSubscriptionRepository(session).has_entitled_subscription(
        "user-1", exclude_sandbox=exclude_sandbox
    )
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    where = str(compiled).split("WHERE", 1)[1]
    return where, compiled.params


@pytest.mark.asyncio
async def test_only_active_and_billing_issue_rows_count_until_expiry():
    """Cancelled rows go to RevenueCat: refunds arrive as CANCELLATION too."""
    where, params = await _compiled_query(exclude_sandbox=False)

    assert ENTITLED_SUBSCRIPTION_STATUSES == ("active", "billing_issue")
    assert "subscriptions.status IN" in where
    assert list(ENTITLED_SUBSCRIPTION_STATUSES) in params.values()


@pytest.mark.asyncio
async def test_expired_rows_do_not_count():
    before = utc_now()
    where, params = await _compiled_query(exclude_sandbox=False)

    assert "subscriptions.expires_at > %(expires_at_1)s" in where
    assert before <= params["expires_at_1"] <= utc_now() + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_sandbox_rows_are_dropped_only_when_asked():
    production_where, _ = await _compiled_query(exclude_sandbox=True)
    other_where, _ = await _compiled_query(exclude_sandbox=False)

    assert "subscriptions.is_sandbox IS false" in production_where
    assert "is_sandbox" not in other_where


@pytest.mark.asyncio
async def test_a_null_expiry_counts_only_for_a_lifetime_product():
    """A lifetime row never expires; any other null expiry still goes to RevenueCat."""
    where, params = await _compiled_query(exclude_sandbox=False)

    assert "subscriptions.expires_at IS NULL" in where
    assert "subscriptions.product_id IN" in where
    lifetime_params = [
        value for value in params.values() if value == sorted(LIFETIME_PRODUCT_IDS)
    ]
    assert lifetime_params == [sorted(LIFETIME_PRODUCT_IDS)]


def _row(product_id, *, status="active", expires_at=None, purchased_days_ago=0):
    return Subscription(
        id=str(uuid.uuid4()),
        user_id="user-1",
        revenuecat_subscriber_id="firebase-uid-1",
        product_id=product_id,
        platform="ios",
        status=status,
        purchased_at=utc_now() - timedelta(days=purchased_days_ago),
        expires_at=expires_at,
    )


async def _stored(uow, *subscriptions):
    for subscription in subscriptions:
        uow.session.add(subscription)
    commit(uow)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product_id", "status", "expires_in_days", "granted"),
    [
        ("premium_monthly", "active", None, False),
        (LIFETIME_PRODUCT_ID, "active", None, True),
        (LIFETIME_PRODUCT_ID, "billing_issue", None, True),
        (LIFETIME_PRODUCT_ID, "refunded", None, False),
        ("premium_monthly", "active", 3, True),
        ("premium_monthly", "active", -3, False),
        ("premium_monthly", "cancelled", 3, False),
    ],
)
async def test_the_query_counts_a_null_expiry_only_for_a_lifetime_product(
    product_id, status, expires_in_days, granted
):
    """Runs the real statement against SQLite, not just its compiled text."""
    expires_at = (
        None if expires_in_days is None else utc_now() + timedelta(days=expires_in_days)
    )
    with open_subscription_uow() as uow:
        await _stored(uow, _row(product_id, status=status, expires_at=expires_at))

        assert (
            await uow.subscriptions.has_entitled_subscription(
                "user-1", exclude_sandbox=True
            )
            is granted
        )


@pytest.mark.asyncio
async def test_a_subscribers_rows_come_newest_purchase_first():
    with open_subscription_uow() as uow:
        await _stored(
            uow,
            _row("premium_monthly", purchased_days_ago=90),
            _row(LIFETIME_PRODUCT_ID, purchased_days_ago=1),
            _row("premium_yearly", purchased_days_ago=30),
        )

        found = await uow.subscriptions.find_all_by_revenuecat_id("firebase-uid-1")

    assert [row.product_id for row in found] == [
        LIFETIME_PRODUCT_ID,
        "premium_yearly",
        "premium_monthly",
    ]


@pytest.mark.asyncio
async def test_the_active_subscription_is_lifetime_before_any_plan():
    with open_subscription_uow() as uow:
        await _stored(
            uow,
            _row("premium_yearly", expires_at=utc_now() + timedelta(days=300)),
            _row(LIFETIME_PRODUCT_ID, purchased_days_ago=1),
        )

        active = await uow.subscriptions.find_active_by_user_id("user-1")

    assert active.product_id == LIFETIME_PRODUCT_ID

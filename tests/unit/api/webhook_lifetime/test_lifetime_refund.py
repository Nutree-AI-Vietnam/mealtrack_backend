"""Lifetime refunds: only that purchase's row, and the plan someone still pays for."""

import uuid
from datetime import timedelta

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
    MONTHLY,
    SUBSCRIBER,
    USER,
    YEARLY,
    commit,
    entitled,
    event,
    lifecycle_types,
    lifetime,
    ms,
    rows,
    yearly,
)

from src.api.exceptions import ExternalServiceException
from src.api.routes.v1.webhook_lifetime_purchase import (
    handle_lifetime_purchase,
    handle_lifetime_refund,
)
from src.api.routes.v1.webhooks import handle_purchase, handle_renewal
from src.domain.ports.subscription_service_port import EntitlementCheck
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

pytestmark = pytest.mark.asyncio

UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID


def _refund(product=LIFETIME_PRODUCT_ID, event_type="CANCELLATION", **fields):
    return event(event_type, product, cancel_reason="CUSTOMER_SUPPORT", **fields)


def _yearly_info(days=300):
    return {
        "product_id": YEARLY,
        "expires_date": utc_now() + timedelta(days=days),
        "store": "app_store",
    }


async def _upgrade_from_yearly(deliver) -> None:
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))
    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))


def _by_product(all_rows):
    return {row.product_id: row for row in all_rows}


@pytest.mark.parametrize("event_type", ["CANCELLATION", "REFUND"])
async def test_a_lifetime_refund_marks_the_row_refunded_and_revokes_the_referral(
    uow, deliver, publisher, event_type
):
    """RevenueCat reports a one-time refund as CANCELLATION; REFUND is kept too."""
    await deliver(handle_lifetime_purchase, lifetime())

    await deliver(handle_lifetime_refund, _refund(event_type=event_type))

    (row,) = await rows(uow)
    assert (row.status, row.product_id) == ("refunded", LIFETIME_PRODUCT_ID)
    assert not await entitled(uow)
    assert lifecycle_types(publisher)[-1] == "subscription_refund"
    assert uow.referrals.conversion.status == "revoked"
    assert uow.referrals.balance == 0


async def test_an_upgrade_refund_while_the_yearly_plan_is_paid_restores_it(
    uow, deliver, revenuecat
):
    await _upgrade_from_yearly(deliver)
    (converted,) = await rows(uow)
    yearly_start = converted.purchased_at
    revenuecat.get_subscription_info.return_value = _yearly_info()

    await deliver(handle_lifetime_refund, _refund(UPGRADE))

    by_product = _by_product(await rows(uow))
    assert by_product[UPGRADE].status == "refunded"
    restored = by_product[YEARLY]
    assert (restored.status, restored.user_id) == ("active", USER.id)
    assert restored.expires_at.date() == (utc_now() + timedelta(days=300)).date()
    assert restored.store_transaction_id is None
    assert restored.purchased_at == yearly_start
    assert await entitled(uow)
    # The buyer still pays yearly, so the referral stands.
    assert uow.referrals.conversion.status == "converted"
    assert uow.referrals.balance == 50_000


async def test_the_restored_yearly_plan_takes_its_next_renewal(
    uow, deliver, revenuecat
):
    await _upgrade_from_yearly(deliver)
    revenuecat.get_subscription_info.return_value = _yearly_info(days=10)
    await deliver(handle_lifetime_refund, _refund(UPGRADE))

    await deliver(handle_renewal, yearly("RENEWAL", expiration_at_ms=ms(375)))

    restored = _by_product(await rows(uow))[YEARLY]
    assert restored.expires_at.date() == (utc_now() + timedelta(days=375)).date()
    assert len(await rows(uow)) == 2


def _revenuecat_down(revenuecat, how):
    if how == "lookup raises":
        revenuecat.get_subscription_info.side_effect = RuntimeError("RevenueCat down")
    else:  # the adapter reports an HTTP failure as "no plan"; the check says why
        revenuecat.check_entitlement.return_value = EntitlementCheck.UNAVAILABLE


@pytest.mark.parametrize("how", ["lookup raises", "no answer"])
async def test_an_upgrade_refund_without_revenuecat_is_retried_before_any_write(
    uow, deliver, publisher, revenuecat, caplog, how
):
    """The yearly plan under the upgrade may be paid: wait for a real answer."""
    await _upgrade_from_yearly(deliver)
    published = publisher.publish.await_count
    _revenuecat_down(revenuecat, how)

    with pytest.raises(ExternalServiceException):
        await deliver(handle_lifetime_refund, _refund(UPGRADE))
    uow.session.sync.rollback()  # the unit of work ends without a commit

    (row,) = await rows(uow)
    assert (row.product_id, row.status) == (UPGRADE, "active")
    assert publisher.publish.await_count == published
    assert uow.referrals.conversion.status == "converted"
    # ERROR, so a refund whose every retry fails reaches Sentry.
    (deferred,) = [r for r in caplog.records if "refund deferred" in r.getMessage()]
    assert deferred.levelname == "ERROR"
    assert f"event_id=evt-CANCELLATION-{UPGRADE}" in deferred.getMessage()
    assert SUBSCRIBER not in deferred.getMessage()

    # RevenueCat redelivers the refund once it answers again.
    revenuecat.get_subscription_info.side_effect = None
    revenuecat.get_subscription_info.return_value = _yearly_info()
    await deliver(handle_lifetime_refund, _refund(UPGRADE))

    by_product = _by_product(await rows(uow))
    assert (by_product[UPGRADE].status, by_product[YEARLY].status) == (
        "refunded",
        "active",
    )
    assert uow.referrals.conversion.status == "converted"


@pytest.mark.parametrize(
    ("other_row", "fields"),
    [
        ("web-funnel plan", {"platform": "web"}),
        ("sandbox plan", {"is_sandbox": True}),
    ],
)
async def test_the_restore_never_overwrites_another_platform_or_environment(
    uow, deliver, revenuecat, other_row, fields
):
    await _upgrade_from_yearly(deliver)
    other = _plan_row(MONTHLY, **fields)
    uow.session.add(other)
    commit(uow)
    revenuecat.get_subscription_info.return_value = _yearly_info()

    await deliver(handle_lifetime_refund, _refund(UPGRADE))

    by_product = _by_product(await rows(uow))
    assert by_product[MONTHLY].status == "expired"
    assert by_product[MONTHLY].platform == fields.get("platform", "ios")
    assert (by_product[YEARLY].status, by_product[YEARLY].platform) == ("active", "ios")
    assert not by_product[YEARLY].is_sandbox


def _plan_row(product_id, *, platform="ios", is_sandbox=False):
    return Subscription(
        id=str(uuid.uuid4()),
        user_id=USER.id,
        revenuecat_subscriber_id=SUBSCRIBER,
        product_id=product_id,
        platform=platform,
        status="expired",
        purchased_at=utc_now() - timedelta(days=60),
        expires_at=utc_now() - timedelta(days=30),
        is_sandbox=is_sandbox,
    )


@pytest.mark.parametrize(
    ("product", "answer", "revoked"),
    [
        # RevenueCat answered: the yearly plan is gone too.
        (UPGRADE, EntitlementCheck.NOT_ENTITLED, True),
        # Access from elsewhere RevenueCat knows about keeps the referral.
        (LIFETIME_PRODUCT_ID, EntitlementCheck.ENTITLED, False),
        # A lifetime bought outright implies no other paid plan.
        (LIFETIME_PRODUCT_ID, EntitlementCheck.UNAVAILABLE, True),
    ],
)
async def test_the_referral_follows_revenuecats_answer_after_a_refund(
    uow, deliver, revenuecat, product, answer, revoked
):
    await deliver(handle_lifetime_purchase, lifetime(product))
    revenuecat.check_entitlement.return_value = answer

    await deliver(handle_lifetime_refund, _refund(product))

    assert (uow.referrals.conversion.status == "revoked") is revoked


async def test_a_lifetime_refund_updates_a_stale_plan_row_rather_than_adding_one(
    uow, deliver, revenuecat
):
    """The plan renewed without its webhook landing, so its row is stale."""
    await deliver(
        handle_purchase, event("INITIAL_PURCHASE", MONTHLY, expiration_at_ms=ms(-1))
    )
    await deliver(handle_lifetime_purchase, lifetime())
    revenuecat.get_subscription_info.return_value = {
        "product_id": MONTHLY,
        "expires_date": utc_now() + timedelta(days=20),
        "store": "app_store",
    }

    await deliver(handle_lifetime_refund, _refund())

    by_product = _by_product(await rows(uow))
    assert len(by_product) == 2
    assert by_product[MONTHLY].status == "active"
    assert (
        by_product[MONTHLY].expires_at.date() == (utc_now() + timedelta(days=20)).date()
    )
    assert await entitled(uow)


async def test_a_lifetime_refund_hits_the_lifetime_row_among_several(uow, deliver):
    """An older expired plan row sits first for the same subscriber."""
    uow.session.add(
        Subscription(
            id=str(uuid.uuid4()),
            user_id=USER.id,
            revenuecat_subscriber_id=SUBSCRIBER,
            product_id=MONTHLY,
            platform="ios",
            status="expired",
            purchased_at=utc_now() - timedelta(days=90),
            expires_at=utc_now() - timedelta(days=60),
        )
    )
    commit(uow)
    await deliver(handle_lifetime_purchase, lifetime())

    await deliver(handle_lifetime_refund, _refund())

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "expired", LIFETIME_PRODUCT_ID: "refunded"}
    assert not await entitled(uow)


async def test_a_refund_matches_its_transaction_before_its_product(uow, deliver):
    """An old refunded purchase and a new one: the old refund's redelivery."""
    await deliver(handle_lifetime_purchase, lifetime(transaction_id="tx-old"))
    await deliver(handle_lifetime_refund, _refund(transaction_id="tx-old"))
    await deliver(handle_lifetime_purchase, lifetime(transaction_id="tx-new"))
    (row,) = await rows(uow)
    assert (row.status, row.store_transaction_id) == ("active", "tx-new")

    await deliver(handle_lifetime_refund, _refund(transaction_id="tx-new"))
    (row,) = await rows(uow)
    assert row.status == "refunded"


def _lifetime_row(transaction_id, *, days_ago, sandbox=False):
    return Subscription(
        id=str(uuid.uuid4()),
        user_id=USER.id,
        revenuecat_subscriber_id=SUBSCRIBER,
        product_id=LIFETIME_PRODUCT_ID,
        platform="ios",
        status="active",
        purchased_at=utc_now() - timedelta(days=days_ago),
        expires_at=None,
        store_transaction_id=transaction_id,
        is_sandbox=sandbox,
    )


async def test_a_refund_hits_its_own_purchase_when_two_rows_share_the_product(
    uow, deliver
):
    """A transfer merged two accounts that each bought lifetime."""
    uow.session.add(_lifetime_row("tx-a", days_ago=30))
    uow.session.add(_lifetime_row("tx-b", days_ago=1))
    commit(uow)

    await deliver(handle_lifetime_refund, _refund(transaction_id="tx-a"))

    statuses = {row.store_transaction_id: row.status for row in await rows(uow)}
    assert statuses == {"tx-a": "refunded", "tx-b": "active"}
    assert await entitled(uow)
    assert uow.referrals.conversion.status != "revoked"


async def test_a_sandbox_refund_never_falls_back_to_a_production_row(uow, deliver):
    uow.session.add(_lifetime_row("tx-production", days_ago=1))
    commit(uow)

    sandbox_refund = _refund(transaction_id="tx-sandbox", environment="SANDBOX")
    await deliver(handle_lifetime_refund, sandbox_refund)

    (row,) = await rows(uow)
    assert row.status == "active"
    assert await entitled(uow)


async def test_a_redelivered_refund_never_refunds_the_restored_plan(
    uow, deliver, revenuecat
):
    await _upgrade_from_yearly(deliver)
    revenuecat.get_subscription_info.return_value = _yearly_info()
    await deliver(handle_lifetime_refund, _refund(UPGRADE))

    await deliver(handle_lifetime_refund, _refund(UPGRADE))

    by_product = _by_product(await rows(uow))
    assert (by_product[UPGRADE].status, by_product[YEARLY].status) == (
        "refunded",
        "active",
    )
    assert len(by_product) == 2


async def test_a_redelivered_purchase_does_not_reactivate_a_refund(uow, deliver):
    await deliver(handle_lifetime_purchase, lifetime())
    await deliver(handle_lifetime_refund, _refund())

    await deliver(handle_lifetime_purchase, lifetime())

    (row,) = await rows(uow)
    assert row.status == "refunded"
    assert not await entitled(uow)


async def test_a_lifetime_refund_leaves_a_plan_row_alone(uow, deliver):
    """The lifetime webhook never landed, so the only row is the yearly plan."""
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))

    await deliver(handle_lifetime_refund, _refund())

    (row,) = await rows(uow)
    assert (row.product_id, row.status) == (YEARLY, "active")
    assert await entitled(uow)
    assert uow.referrals.conversion.status == "converted"


async def test_a_lifetime_refund_matching_no_row_writes_nothing(
    uow, deliver, publisher, caplog
):
    await deliver(handle_lifetime_refund, _refund())

    assert await rows(uow) == []
    assert lifecycle_types(publisher) == ["subscription_refund"]
    assert "Lifetime refund matches no row" in caplog.text

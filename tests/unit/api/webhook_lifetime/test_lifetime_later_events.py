"""Renewing-product events once a subscriber has, or had, lifetime."""

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

from src.api.routes.v1.webhook_lifetime_purchase import (
    handle_lifetime_purchase,
    handle_lifetime_refund,
)
from src.api.routes.v1.webhooks import (
    handle_billing_issue,
    handle_cancellation,
    handle_expiration,
    handle_product_change,
    handle_purchase,
    handle_refund,
    handle_renewal,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

pytestmark = pytest.mark.asyncio

UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID
_REPORTED_AS = {
    "INITIAL_PURCHASE": "subscription_initial_purchase",
    "RENEWAL": "subscription_renewal",
    "PRODUCT_CHANGE": "subscription_product_changed",
}
_HANDLERS = {
    "INITIAL_PURCHASE": handle_purchase,
    "RENEWAL": handle_renewal,
    "CANCELLATION": handle_cancellation,
    "EXPIRATION": handle_expiration,
    "BILLING_ISSUE": handle_billing_issue,
    "PRODUCT_CHANGE": handle_product_change,
    "REFUND": handle_refund,
}


async def _upgrade_from_yearly(deliver) -> None:
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))
    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))


def _add_row(uow, product_id, *, status="active", expires_in_days=30, sandbox=False):
    expires_at = (
        None if expires_in_days is None else utc_now() + timedelta(days=expires_in_days)
    )
    uow.session.add(
        Subscription(
            id=str(uuid.uuid4()),
            user_id=USER.id,
            revenuecat_subscriber_id=SUBSCRIBER,
            product_id=product_id,
            platform="web",
            status=status,
            purchased_at=utc_now() - timedelta(days=10),
            expires_at=expires_at,
            is_sandbox=sandbox,
        )
    )
    commit(uow)


# --- After an upgrade the yearly plan's events change no row ---


@pytest.mark.parametrize("event_type", sorted(_HANDLERS))
async def test_a_later_yearly_event_leaves_the_lifetime_row_alone(
    uow, deliver, event_type
):
    await _upgrade_from_yearly(deliver)
    (before,) = await rows(uow)
    snapshot = (before.product_id, before.status, before.expires_at, before.updated_at)

    await deliver(_HANDLERS[event_type], yearly(event_type))

    (row,) = await rows(uow)
    assert (row.product_id, row.status, row.expires_at, row.updated_at) == snapshot
    assert await entitled(uow)


async def test_a_yearly_renewal_after_the_upgrade_is_still_reported(
    uow, deliver, publisher
):
    await _upgrade_from_yearly(deliver)

    await deliver(handle_renewal, yearly("RENEWAL"))

    assert lifecycle_types(publisher)[-1] == "subscription_renewal"


async def test_a_yearly_billing_issue_after_the_upgrade_sends_no_push(
    uow, deliver, publisher
):
    await _upgrade_from_yearly(deliver)

    await deliver(handle_billing_issue, yearly("BILLING_ISSUE"))

    uow.engagement_pushes.enqueue.assert_not_awaited()
    assert lifecycle_types(publisher)[-1] == "subscription_billing_issue"


@pytest.mark.parametrize("event_type", ["REFUND", "CANCELLATION"])
async def test_a_yearly_refund_after_the_upgrade_keeps_lifetime_and_the_referral(
    uow, deliver, event_type
):
    await _upgrade_from_yearly(deliver)

    refund = yearly(event_type, cancel_reason="CUSTOMER_SUPPORT")
    await deliver(_HANDLERS[event_type], refund)

    (row,) = await rows(uow)
    assert (row.product_id, row.status) == (UPGRADE, "active")
    assert await entitled(uow)
    assert uow.referrals.conversion.status == "converted"
    assert uow.referrals.balance == 50_000


# --- After a lifetime refund the next plan is stored as usual ---


@pytest.mark.parametrize(
    "event_type", ["INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE"]
)
async def test_a_plan_after_a_lifetime_refund_gets_its_own_active_row(
    uow, deliver, publisher, event_type
):
    await deliver(handle_lifetime_purchase, lifetime())
    await deliver(handle_lifetime_refund, event("CANCELLATION", LIFETIME_PRODUCT_ID))
    assert not await entitled(uow)

    plan = event(event_type, MONTHLY, expiration_at_ms=ms(30))
    await deliver(_HANDLERS[event_type], plan)

    by_product = {row.product_id: row for row in await rows(uow)}
    assert by_product[LIFETIME_PRODUCT_ID].status == "refunded"
    assert by_product[MONTHLY].status == "active"
    assert by_product[MONTHLY].expires_at is not None
    assert await entitled(uow)
    # A RENEWAL with no row is stored, but still reported as a renewal.
    assert lifecycle_types(publisher)[-1] == _REPORTED_AS[event_type]


async def test_a_plan_after_a_lifetime_refund_renews_and_changes_product(uow, deliver):
    await deliver(handle_lifetime_purchase, lifetime())
    await deliver(handle_lifetime_refund, event("CANCELLATION", LIFETIME_PRODUCT_ID))
    first = event("INITIAL_PURCHASE", MONTHLY, expiration_at_ms=ms(30))
    await deliver(handle_purchase, first)

    await deliver(handle_renewal, event("RENEWAL", MONTHLY, expiration_at_ms=ms(60)))
    monthly = next(row for row in await rows(uow) if row.product_id == MONTHLY)
    assert monthly.expires_at.date() == (utc_now() + timedelta(days=60)).date()

    change = event("PRODUCT_CHANGE", YEARLY, expiration_at_ms=ms(365))
    await deliver(handle_product_change, change)
    products = sorted(row.product_id for row in await rows(uow))
    assert products == sorted([LIFETIME_PRODUCT_ID, YEARLY])
    assert await entitled(uow)


# --- Several rows for one subscriber ---


async def test_a_plan_beside_lifetime_still_tracks_its_own_row(uow, deliver):
    """A web-funnel row for another product sits beside the lifetime row."""
    _add_row(uow, MONTHLY)
    await deliver(handle_lifetime_purchase, lifetime())

    await deliver(handle_expiration, event("EXPIRATION", MONTHLY))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "expired", LIFETIME_PRODUCT_ID: "active"}


@pytest.mark.parametrize("event_type", ["RENEWAL", "EXPIRATION", "PRODUCT_CHANGE"])
async def test_beside_lifetime_a_plan_without_its_own_row_changes_nothing(
    uow, deliver, event_type
):
    """No fallback onto another plan's row and no new row while lifetime holds."""
    _add_row(uow, MONTHLY)
    await deliver(handle_lifetime_purchase, lifetime())

    await deliver(_HANDLERS[event_type], yearly(event_type))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "active", LIFETIME_PRODUCT_ID: "active"}


async def test_a_billing_issue_beside_lifetime_flags_the_plan_without_a_push(
    uow, deliver
):
    _add_row(uow, MONTHLY)
    await deliver(handle_lifetime_purchase, lifetime())

    await deliver(handle_billing_issue, event("BILLING_ISSUE", MONTHLY))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "billing_issue", LIFETIME_PRODUCT_ID: "active"}
    uow.engagement_pushes.enqueue.assert_not_awaited()


async def test_a_plan_that_ended_beside_lifetime_is_not_reported_after_a_refund(
    uow, deliver
):
    """The yearly plan's end lands on its row, so sync_user agrees with the gate."""
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE", expiration_at_ms=ms(5)))
    await deliver(handle_lifetime_purchase, lifetime())
    await deliver(handle_cancellation, yearly("CANCELLATION"))
    await deliver(handle_expiration, yearly("EXPIRATION"))

    await deliver(handle_lifetime_refund, event("CANCELLATION", LIFETIME_PRODUCT_ID))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {YEARLY: "expired", LIFETIME_PRODUCT_ID: "refunded"}
    assert not await entitled(uow)
    assert await uow.subscriptions.find_active_by_user_id(USER.id) is None


async def test_a_plan_event_acts_on_its_own_row_among_several(uow, deliver):
    _add_row(uow, MONTHLY)
    _add_row(uow, YEARLY, expires_in_days=200)

    await deliver(handle_expiration, event("EXPIRATION", MONTHLY))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "expired", YEARLY: "active"}


async def test_a_sandbox_lifetime_row_does_not_freeze_production_events(uow, deliver):
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))
    _add_row(uow, LIFETIME_PRODUCT_ID, expires_in_days=None, sandbox=True)

    await deliver(handle_expiration, yearly("EXPIRATION"))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {YEARLY: "expired", LIFETIME_PRODUCT_ID: "active"}


async def test_a_renewing_refund_keeps_the_referral_while_another_plan_is_paid(
    uow, deliver
):
    """A web-funnel yearly row and an app monthly row for one subscriber."""
    _add_row(uow, YEARLY, expires_in_days=200)
    _add_row(uow, MONTHLY)
    uow.referrals.conversion.status = "converted"

    await deliver(handle_refund, event("REFUND", MONTHLY))

    statuses = {row.product_id: row.status for row in await rows(uow)}
    assert statuses == {MONTHLY: "refunded", YEARLY: "active"}
    assert uow.referrals.conversion.status == "converted"


async def test_a_renewing_refund_that_ends_paid_access_revokes_the_referral(
    uow, deliver
):
    await deliver(
        handle_purchase, event("INITIAL_PURCHASE", MONTHLY, expiration_at_ms=ms(30))
    )

    await deliver(handle_refund, event("REFUND", MONTHLY))

    assert not await entitled(uow)
    assert uow.referrals.conversion.status == "revoked"
    assert uow.referrals.balance == 0

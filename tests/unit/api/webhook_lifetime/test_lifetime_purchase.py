"""A lifetime purchase: its row, the upgrade, the environment and what is reported."""

from types import SimpleNamespace

import pytest
from tests.fixtures.fakes.lifetime_webhook_db import (
    LIFETIME_PRODUCT_ID,
    LIFETIME_UPGRADE_PRODUCT_ID,
    MONTHLY,
    SUBSCRIBER,
    USER,
    YEARLY,
    entitled,
    event,
    lifecycle_types,
    lifetime,
    ms,
    rows,
    yearly,
)

from src.api.routes.v1.webhook_lifetime_purchase import handle_lifetime_purchase
from src.api.routes.v1.webhooks import (
    handle_cancellation,
    handle_expiration,
    handle_purchase,
)

pytestmark = pytest.mark.asyncio

UPGRADE = LIFETIME_UPGRADE_PRODUCT_ID


def _published(publisher) -> list[tuple[str, str]]:
    return [
        (call.args[0]["data"]["lifecycle_type"], call.args[0]["data"]["product_id"])
        for call in publisher.publish.await_args_list
    ]


# --- The row ---


@pytest.mark.parametrize("product", [LIFETIME_PRODUCT_ID, UPGRADE])
async def test_a_lifetime_purchase_stores_an_active_row_that_never_expires(
    uow, deliver, product
):
    await deliver(handle_lifetime_purchase, lifetime(product))

    (row,) = await rows(uow)
    assert (row.product_id, row.status, row.expires_at) == (product, "active", None)
    assert (row.user_id, row.revenuecat_subscriber_id) == (USER.id, SUBSCRIBER)
    assert (row.platform, row.is_sandbox) == ("ios", False)
    assert row.store_transaction_id == f"tx-{product}"
    assert row.is_lifetime() and row.is_active()
    assert await entitled(uow)


async def test_a_play_suffix_is_matched_and_stored_as_the_base_id(uow, deliver):
    play_id = f"{LIFETIME_PRODUCT_ID}:buy"
    await deliver(handle_lifetime_purchase, lifetime(play_id, store="PLAY_STORE"))

    (row,) = await rows(uow)
    assert (row.product_id, row.platform) == (LIFETIME_PRODUCT_ID, "android")
    assert await entitled(uow)


async def test_the_upgrade_turns_the_yearly_row_into_lifetime(uow, deliver):
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))
    (yearly_row,) = await rows(uow)
    yearly_id, purchased_at = yearly_row.id, yearly_row.purchased_at

    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))

    (row,) = await rows(uow)
    assert (row.id, row.purchased_at) == (yearly_id, purchased_at)
    assert (row.product_id, row.status, row.expires_at) == (UPGRADE, "active", None)
    assert row.store_transaction_id == f"tx-{UPGRADE}"
    assert await entitled(uow)


async def test_an_upgrade_after_turning_off_renewal_converts_the_yearly_row(
    uow, deliver
):
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))
    await deliver(handle_cancellation, yearly("CANCELLATION"))

    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))

    (row,) = await rows(uow)
    assert (row.product_id, row.status) == (UPGRADE, "active")
    assert (row.cancelled_at, row.expires_at) == (None, None)
    assert await entitled(uow)


async def test_converting_a_row_moves_it_to_the_buyer(uow, deliver):
    previous_owner = SimpleNamespace(id="previous-owner")
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"), user=previous_owner)

    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))

    (row,) = await rows(uow)
    assert row.user_id == USER.id
    assert not await entitled(uow, previous_owner.id)


async def test_an_ended_plan_is_kept_as_history_beside_a_new_lifetime_row(uow, deliver):
    lapsed = event("INITIAL_PURCHASE", MONTHLY, expiration_at_ms=ms(-30))
    await deliver(handle_purchase, lapsed)
    await deliver(handle_expiration, event("EXPIRATION", MONTHLY))

    await deliver(handle_lifetime_purchase, lifetime())

    by_product = {row.product_id: row for row in await rows(uow)}
    assert by_product[MONTHLY].status == "expired"
    assert by_product[LIFETIME_PRODUCT_ID].status == "active"


# --- The environment ---


async def test_a_sandbox_purchase_leaves_a_production_row_alone(uow, deliver, caplog):
    caplog.set_level("INFO")
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))

    await deliver(handle_lifetime_purchase, lifetime(UPGRADE, environment="SANDBOX"))

    production, sandbox = sorted(await rows(uow), key=lambda row: row.is_sandbox)
    assert (production.product_id, production.status) == (YEARLY, "active")
    assert production.expires_at is not None and not production.is_sandbox
    assert (sandbox.product_id, sandbox.is_sandbox) == (UPGRADE, True)
    assert "leaves the other environment's rows alone" in caplog.text


async def test_a_sandbox_lifetime_row_grants_nothing_in_production(uow, deliver):
    await deliver(handle_lifetime_purchase, lifetime(environment="SANDBOX"))

    assert not await entitled(uow, exclude_sandbox=True)
    assert await entitled(uow, exclude_sandbox=False)


# --- Redeliveries and bad input ---


async def test_a_redelivered_purchase_is_a_no_op(uow, deliver, publisher):
    await deliver(handle_lifetime_purchase, lifetime())
    (stored,) = await rows(uow)
    updated_at = stored.updated_at

    await deliver(handle_lifetime_purchase, lifetime())

    (row,) = await rows(uow)
    assert (row.id, row.updated_at) == (stored.id, updated_at)
    assert lifecycle_types(publisher) == ["subscription_initial_purchase"]
    assert uow.referrals.balance == 50_000


async def test_a_purchase_without_a_subscriber_id_is_logged_without_a_write(
    uow, deliver, publisher, caplog
):
    await deliver(handle_lifetime_purchase, lifetime(app_user_id=None))

    assert await rows(uow) == []
    assert publisher.publish.await_count == 0
    assert "Lifetime purchase without app_user_id" in caplog.text


# --- What is reported: one initial purchase per buyer ---


async def test_a_first_lifetime_purchase_is_the_initial_purchase(
    uow, deliver, publisher
):
    await deliver(handle_lifetime_purchase, lifetime())

    assert _published(publisher) == [
        ("subscription_initial_purchase", LIFETIME_PRODUCT_ID)
    ]
    data = publisher.publish.await_args.args[0]["data"]
    assert data["subscription_id"] == f"tx-{LIFETIME_PRODUCT_ID}"


async def test_the_upgrade_is_not_a_second_initial_purchase(uow, deliver, publisher):
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))
    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))

    assert _published(publisher) == [("subscription_initial_purchase", YEARLY)]


async def test_the_yearly_purchase_after_the_upgrade_webhook_is_the_initial_one(
    uow, deliver, publisher
):
    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))

    assert _published(publisher) == [("subscription_initial_purchase", YEARLY)]
    (row,) = await rows(uow)
    assert (row.product_id, row.status, row.expires_at) == (UPGRADE, "active", None)
    assert row.store_transaction_id == f"tx-{UPGRADE}"


async def test_lifetime_bought_by_a_past_subscriber_is_not_reported_again(
    uow, deliver, publisher
):
    lapsed = event("INITIAL_PURCHASE", MONTHLY, expiration_at_ms=ms(-30))
    await deliver(handle_purchase, lapsed)

    await deliver(handle_lifetime_purchase, lifetime())

    assert _published(publisher) == [("subscription_initial_purchase", MONTHLY)]


# --- Referral credit ---


async def test_a_first_lifetime_purchase_credits_the_referrer(uow, deliver):
    await deliver(handle_lifetime_purchase, lifetime())

    assert uow.referrals.conversion.status == "converted"
    assert uow.referrals.balance == 50_000


async def test_the_upgrade_does_not_credit_the_referrer_twice(uow, deliver):
    await deliver(handle_purchase, yearly("INITIAL_PURCHASE"))
    await deliver(handle_lifetime_purchase, lifetime(UPGRADE))

    assert uow.referrals.conversion.status == "converted"
    assert uow.referrals.balance == 50_000

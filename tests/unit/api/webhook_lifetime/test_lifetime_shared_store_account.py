"""One store account behind two Nutree accounts, and stores with no certain platform.

In production some subscriber ids hold two rows of the same product on two
user ids (one Apple ID, two Nutree accounts). Lifetime paths prefer the event
user's row and hand the row they update to that user.
"""

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
    lifetime,
    ms,
    rows,
    yearly,
)

from src.api.routes.v1.webhook_lifetime_purchase import (
    handle_lifetime_purchase,
    handle_lifetime_refund,
)
from src.api.routes.v1.webhook_lookup_parsing import store_platform
from src.api.routes.v1.webhook_referral_funnel import (
    handle_transfer,
    sync_redeemed_target_subscription,
)
from src.api.routes.v1.webhooks import (
    handle_cancellation,
    handle_expiration,
    handle_renewal,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

pytestmark = pytest.mark.asyncio

OTHER = "user-other"  # a second Nutree account on the same store account
OLD_UID = "uid-old"


def _add(uow, user_id, product, *, days_ago, expires_in, platform="ios", **fields):
    uow.session.add(
        Subscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            revenuecat_subscriber_id=fields.pop("subscriber", SUBSCRIBER),
            product_id=product,
            platform=platform,
            status=fields.pop("status", "active"),
            purchased_at=utc_now() - timedelta(days=days_ago),
            expires_at=utc_now() + timedelta(days=expires_in),
            **fields,
        )
    )


def _yearly_info(days=300, store="app_store", product=YEARLY):
    expires = utc_now() + timedelta(days=days)
    return {"product_id": product, "store": store, "expires_date": expires}


def _owners(all_rows):
    return {(row.user_id, row.product_id): row.status for row in all_rows}


# --- W1: the event user's row, handed to the event user ---


@pytest.mark.parametrize("other_live", [True, False])
async def test_after_an_upgrade_the_yearly_plan_stays_with_the_buyer(
    uow, deliver, revenuecat, other_live
):
    """Plan events while lifetime holds, then the refund's restore."""
    _add(uow, OTHER, YEARLY, days_ago=60, expires_in=300 if other_live else -5)
    _add(uow, USER.id, YEARLY, days_ago=1, expires_in=364)
    commit(uow)
    await deliver(handle_lifetime_purchase, lifetime(LIFETIME_UPGRADE_PRODUCT_ID))

    await deliver(handle_renewal, yearly("RENEWAL", expiration_at_ms=ms(700)))
    await deliver(handle_cancellation, yearly("CANCELLATION", expiration_at_ms=ms(700)))
    yearly_rows = [row for row in await rows(uow) if row.product_id == YEARLY]
    assert [(row.user_id, row.status) for row in yearly_rows] == [
        (USER.id, "cancelled")
    ]

    revenuecat.get_subscription_info.return_value = _yearly_info(days=700)
    await deliver(
        handle_lifetime_refund, event("CANCELLATION", LIFETIME_UPGRADE_PRODUCT_ID)
    )

    assert _owners(await rows(uow)) == {
        (USER.id, YEARLY): "active",
        (USER.id, LIFETIME_UPGRADE_PRODUCT_ID): "refunded",
    }
    assert await entitled(uow)
    assert not await entitled(uow, OTHER)


async def test_beside_lifetime_a_plan_event_prefers_the_event_users_row(uow, deliver):
    _add(uow, USER.id, YEARLY, days_ago=30, expires_in=335)
    _add(uow, OTHER, YEARLY, days_ago=1, expires_in=364)  # the newest row
    commit(uow)
    await deliver(handle_lifetime_purchase, lifetime())

    await deliver(handle_expiration, yearly("EXPIRATION"))

    assert _owners(await rows(uow)) == {
        (USER.id, YEARLY): "expired",
        (OTHER, YEARLY): "active",
        (USER.id, LIFETIME_PRODUCT_ID): "active",
    }


async def test_the_restore_prefers_the_event_users_row(uow, deliver, revenuecat):
    _add(uow, USER.id, YEARLY, days_ago=400, expires_in=-35, status="expired")
    _add(uow, OTHER, YEARLY, days_ago=200, expires_in=-5, status="expired")
    commit(uow)
    await deliver(handle_lifetime_purchase, lifetime())
    revenuecat.get_subscription_info.return_value = _yearly_info()

    await deliver(handle_lifetime_refund, event("CANCELLATION", LIFETIME_PRODUCT_ID))

    assert _owners(await rows(uow)) == {
        (USER.id, YEARLY): "active",
        (OTHER, YEARLY): "expired",
        (USER.id, LIFETIME_PRODUCT_ID): "refunded",
    }


# --- W2 and W3: stores with no certain platform ---


@pytest.mark.parametrize(
    ("store", "platform"),
    [
        ("APP_STORE", "ios"),
        ("mac_app_store", "ios"),
        ("PLAY_STORE", "android"),
        ("RC_BILLING", "web"),
        ("rc_billing", "web"),
        ("PADDLE", "web"),
        ("stripe", "web"),
        ("PROMOTIONAL", None),
        ("AMAZON", None),
        (None, None),
    ],
)
async def test_only_stores_with_a_certain_platform_filter_rows(store, platform):
    assert store_platform(store) == platform


async def _transfer(uow, store):
    payload = {
        "type": "TRANSFER",
        "store": store,
        "transferred_from": [OLD_UID],
        "transferred_to": [SUBSCRIBER],
    }
    await handle_transfer(uow, payload)
    commit(uow)


async def test_an_rc_billing_transfer_moves_the_web_plan_not_the_app_plan(uow):
    _add(
        uow,
        OTHER,
        "web_yearly",
        days_ago=10,
        expires_in=300,
        platform="web",
        subscriber=OLD_UID,
    )
    _add(uow, OTHER, MONTHLY, days_ago=5, expires_in=25, subscriber=OLD_UID)
    commit(uow)

    await _transfer(uow, "RC_BILLING")

    moved = {row.product_id: row.revenuecat_subscriber_id for row in await rows(uow)}
    assert moved == {"web_yearly": SUBSCRIBER, MONTHLY: OLD_UID}


async def test_a_transfer_from_a_store_with_no_certain_platform_moves_every_row(uow):
    _add(
        uow,
        OTHER,
        "web_yearly",
        days_ago=10,
        expires_in=300,
        platform="web",
        subscriber=OLD_UID,
    )
    _add(uow, OTHER, MONTHLY, days_ago=5, expires_in=25, subscriber=OLD_UID)
    commit(uow)

    await _transfer(uow, "PROMOTIONAL")

    assert {row.revenuecat_subscriber_id for row in await rows(uow)} == {SUBSCRIBER}


async def test_an_rc_billing_redemption_updates_the_web_plan_in_place(uow, revenuecat):
    _add(uow, USER.id, "web_yearly", days_ago=10, expires_in=5, platform="web")
    commit(uow)
    revenuecat.get_subscription_info.return_value = _yearly_info(
        days=365, store="rc_billing", product="web_yearly"
    )
    redeemed = {"type": "PURCHASE_REDEEMED", "store": "RC_BILLING"}

    await sync_redeemed_target_subscription(uow, USER, redeemed)
    commit(uow)

    (row,) = await rows(uow)
    assert (row.product_id, row.platform) == ("web_yearly", "web")
    assert row.expires_at.date() == (utc_now() + timedelta(days=365)).date()


async def test_the_upgrade_converts_the_buyers_running_yearly_row(uow, deliver):
    _add(uow, USER.id, YEARLY, days_ago=30, expires_in=335)
    _add(uow, OTHER, YEARLY, days_ago=1, expires_in=364)  # the newest row
    commit(uow)

    await deliver(handle_lifetime_purchase, lifetime(LIFETIME_UPGRADE_PRODUCT_ID))

    assert _owners(await rows(uow)) == {
        (USER.id, LIFETIME_UPGRADE_PRODUCT_ID): "active",
        (OTHER, YEARLY): "active",
    }

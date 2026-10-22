"""Granting MoMo passes through RevenueCat promotional entitlements."""

from datetime import timedelta

import pytest

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services import momo_pass_entitlement
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements

pytestmark = pytest.mark.asyncio


class GrantSession:
    def __init__(self, user, order, current_end):
        self.user = user
        self.scalars_left = iter([order, current_end])
        self.commits, self.rollbacks = 0, 0

    async def get(self, _model, _identifier, **_kwargs):
        return self.user

    async def scalar(self, _statement):
        return next(self.scalars_left)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeRevenueCat:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    async def grant_promotional_entitlement(
        self, app_user_id, entitlement_id, end_time_ms
    ):
        self.calls.append((app_user_id, entitlement_id, end_time_ms))
        return self.ok


def _user():
    return User(id="user-1", firebase_uid="firebase-uid-1")


def _paid(**values):
    return MomoPaymentOrder(
        order_id="o1", weeks=4, status="paid", granted_at=None, lead_id=None, **values
    )


@pytest.fixture(autouse=True)
def claimed_leads(monkeypatch):
    claimed = []

    async def mark(_db, lead_id, uid):
        claimed.append((lead_id, uid))

    monkeypatch.setattr(momo_pass_entitlement, "mark_lead_claimed", mark)
    return claimed


async def test_grant_stacks_after_the_current_pass_on_the_firebase_uid():
    current_end = utc_now() + timedelta(days=5)
    order = _paid()
    revenuecat = FakeRevenueCat()

    granted = await MomoPassEntitlements(revenuecat).grant(
        GrantSession(_user(), order, current_end), "o1", "user-1"
    )

    assert granted
    assert order.access_starts_at == current_end
    assert order.access_ends_at == current_end + timedelta(weeks=4)
    assert revenuecat.calls == [
        ("firebase-uid-1", "standard", int(order.access_ends_at.timestamp() * 1000))
    ]
    assert order.granted_user_id == "user-1"


async def test_web_pass_grant_claims_its_lead(claimed_leads):
    order = _paid()
    order.lead_id = "lead-1"

    await MomoPassEntitlements(FakeRevenueCat()).grant(
        GrantSession(_user(), order, None), "o1", "user-1"
    )

    assert claimed_leads == [("lead-1", "firebase-uid-1")]


async def test_failed_revenuecat_call_leaves_the_grant_for_a_retry():
    order = _paid()
    session = GrantSession(_user(), order, None)

    granted = await MomoPassEntitlements(FakeRevenueCat(ok=False)).grant(
        session, "o1", "user-1"
    )

    assert not granted
    assert order.granted_at is None and order.access_ends_at is None
    assert session.rollbacks == 1 and session.commits == 0


async def test_granted_order_is_not_granted_again():
    order = _paid()
    order.granted_at = utc_now()
    revenuecat = FakeRevenueCat()

    assert await MomoPassEntitlements(revenuecat).grant(
        GrantSession(_user(), order, None), "o1", "user-1"
    )
    assert revenuecat.calls == []


async def test_unpaid_order_is_never_granted():
    order = _paid()
    order.status = "pending"
    revenuecat = FakeRevenueCat()

    assert not await MomoPassEntitlements(revenuecat).grant(
        GrantSession(_user(), order, None), "o1", "user-1"
    )
    assert revenuecat.calls == []

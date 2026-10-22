"""Granting MoMo passes through RevenueCat promotional entitlements."""

from datetime import timedelta

import pytest
from sqlalchemy.sql.dml import Update

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.momo_payment_order import MomoPaymentOrder
from src.infra.database.models.user.user import User
from src.infra.services import momo_pass_entitlement
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements

pytestmark = pytest.mark.asyncio


class GrantSession:
    """Feeds the locked order and the stacking aggregate; records the confirm."""

    def __init__(self, user, order, current_end=None, selects=None):
        self.user, self.order = user, order
        self.selects_left = iter(selects or [order, current_end])
        self.events = []

    async def get(self, _model, _identifier, **kwargs):
        assert kwargs == {"with_for_update": True, "populate_existing": True}
        return self.user

    async def scalar(self, statement):
        if isinstance(statement, Update):
            self.events.append("confirm")
            if self.order.granted_at is not None:
                return None
            self.order.granted_at = utc_now()
            return 1
        return next(self.selects_left)

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


class FakeRevenueCat:
    def __init__(self, session=None, ok=True, promotional_ends=()):
        self.session, self.ok, self.calls = session, ok, []
        self.promotional_ends, self.reads = promotional_ends, 0

    async def promotional_expiries(self, _app_user_id):
        self.reads += 1
        return None if self.promotional_ends is None else list(self.promotional_ends)

    async def grant_promotional_entitlement(
        self, app_user_id, entitlement_id, end_time_ms
    ):
        # The reservation is committed before RevenueCat is called.
        if self.session:
            assert self.session.events[-1] == "commit"
        self.calls.append((app_user_id, entitlement_id, end_time_ms))
        return self.ok


def _user():
    return User(id="user-1", firebase_uid="firebase-uid-1")


def _paid(**values):
    return MomoPaymentOrder(
        **{
            "order_id": "o1",
            "weeks": 4,
            "status": "paid",
            "granted_at": None,
            "lead_id": None,
            **values,
        }
    )


def _ms(moment):
    return int(moment.timestamp() * 1000)


@pytest.fixture(autouse=True)
def claimed_leads(monkeypatch):
    claimed = []

    async def mark(_db, lead_id, uid):
        claimed.append((lead_id, uid))

    monkeypatch.setattr(momo_pass_entitlement, "mark_lead_claimed", mark)
    return claimed


async def test_grant_reserves_a_stacked_window_then_calls_revenuecat():
    current_end = utc_now() + timedelta(days=5)
    order = _paid()
    session = GrantSession(_user(), order, current_end)
    revenuecat = FakeRevenueCat(session)

    granted = await MomoPassEntitlements(revenuecat).grant(session, "o1", "user-1")

    assert granted
    assert order.granted_user_id == "user-1"
    assert order.access_starts_at == current_end
    assert order.access_ends_at == current_end + timedelta(weeks=4)
    assert revenuecat.calls == [
        ("firebase-uid-1", "standard", _ms(order.access_ends_at))
    ]
    assert session.events == ["commit", "confirm", "commit"]
    assert order.granted_at is not None


async def test_web_pass_grant_claims_its_lead(claimed_leads):
    session = GrantSession(_user(), _paid(lead_id="lead-1"))

    await MomoPassEntitlements(FakeRevenueCat()).grant(session, "o1", "user-1")

    assert claimed_leads == [("lead-1", "firebase-uid-1")]


async def test_failed_revenuecat_call_keeps_the_reservation_for_a_retry():
    order = _paid()
    session = GrantSession(_user(), order)

    granted = await MomoPassEntitlements(FakeRevenueCat(ok=False)).grant(
        session, "o1", "user-1"
    )

    assert not granted
    assert order.granted_user_id == "user-1" and order.access_ends_at is not None
    assert order.granted_at is None
    assert session.events == ["commit"]


async def test_retry_reuses_the_reserved_end_time():
    starts = utc_now() - timedelta(hours=1)
    order = _paid(
        granted_user_id="user-1",
        access_starts_at=starts,
        access_ends_at=starts + timedelta(weeks=4),
    )
    # No stacking aggregate is read: the only select is the locked order.
    session = GrantSession(_user(), order, current_end=utc_now() + timedelta(weeks=9))
    revenuecat = FakeRevenueCat()

    assert await MomoPassEntitlements(revenuecat).grant(session, "o1", "user-1")

    assert order.access_starts_at == starts
    assert revenuecat.calls == [
        ("firebase-uid-1", "standard", _ms(starts + timedelta(weeks=4)))
    ]
    assert revenuecat.reads == 0


def _lapsed(**values):
    starts = utc_now() - timedelta(weeks=5)
    return _paid(
        granted_user_id="user-1",
        access_starts_at=starts,
        access_ends_at=starts + timedelta(weeks=4),
        **values,
    )


async def test_lapsed_window_is_reserved_again_from_now():
    order = _lapsed()
    # Locked order, then the re-reservation's locked order and aggregate.
    session = GrantSession(_user(), order, selects=[order, order, None])
    revenuecat = FakeRevenueCat()
    before = utc_now()

    assert await MomoPassEntitlements(revenuecat).grant(session, "o1", "user-1")

    assert order.access_starts_at >= before
    assert order.access_ends_at - order.access_starts_at == timedelta(weeks=4)
    assert revenuecat.reads == 1
    assert revenuecat.calls == [
        ("firebase-uid-1", "standard", _ms(order.access_ends_at))
    ]
    assert order.granted_at is not None


async def test_lapsed_window_stacks_after_the_latest_promotional_upstream():
    order = _lapsed()
    upstream_end = utc_now() + timedelta(days=3)
    session = GrantSession(_user(), order, selects=[order, order, None])

    await MomoPassEntitlements(FakeRevenueCat(promotional_ends=[upstream_end])).grant(
        session, "o1", "user-1"
    )

    assert order.access_starts_at == upstream_end


async def test_lapsed_window_revenuecat_already_holds_is_confirmed_without_a_grant(
    claimed_leads,
):
    order = _lapsed(lead_id="lead-1")
    lapsed_end = order.access_ends_at
    session = GrantSession(_user(), order)
    revenuecat = FakeRevenueCat(promotional_ends=[lapsed_end + timedelta(minutes=5)])

    assert await MomoPassEntitlements(revenuecat).grant(session, "o1", "user-1")

    assert revenuecat.calls == []
    assert order.access_ends_at == lapsed_end and order.granted_at is not None
    assert claimed_leads == [("lead-1", "firebase-uid-1")]


async def test_lapsed_window_waits_while_revenuecat_cannot_be_read():
    order = _lapsed()
    lapsed_window = (order.access_starts_at, order.access_ends_at)
    session = GrantSession(_user(), order)
    revenuecat = FakeRevenueCat(promotional_ends=None)

    assert not await MomoPassEntitlements(revenuecat).grant(session, "o1", "user-1")

    assert revenuecat.calls == []
    assert (order.access_starts_at, order.access_ends_at) == lapsed_window
    assert order.granted_at is None


async def test_window_reserved_for_another_user_is_restacked_for_this_one():
    order = _paid(granted_user_id="user-2", access_ends_at=utc_now())
    session = GrantSession(_user(), order)

    assert await MomoPassEntitlements(FakeRevenueCat()).grant(session, "o1", "user-1")

    assert order.granted_user_id == "user-1"
    assert order.access_ends_at - order.access_starts_at == timedelta(weeks=4)


async def test_confirm_that_loses_the_race_does_not_claim_the_lead_again(
    claimed_leads,
):
    order = _paid(lead_id="lead-1")
    session = GrantSession(_user(), order)

    class ConfirmedMeanwhile(FakeRevenueCat):
        async def grant_promotional_entitlement(self, *args):
            order.granted_at = utc_now()
            return await super().grant_promotional_entitlement(*args)

    assert await MomoPassEntitlements(ConfirmedMeanwhile()).grant(
        session, "o1", "user-1"
    )
    assert claimed_leads == []


async def test_granted_order_is_not_granted_again():
    order = _paid(
        granted_at=utc_now(), granted_user_id="user-1", access_ends_at=utc_now()
    )
    session = GrantSession(_user(), order)
    revenuecat = FakeRevenueCat()

    assert await MomoPassEntitlements(revenuecat).grant(session, "o1", "user-1")
    assert revenuecat.calls == []
    assert "confirm" not in session.events


async def test_unpaid_order_is_never_granted():
    session = GrantSession(_user(), _paid(status="pending"))
    revenuecat = FakeRevenueCat()

    assert not await MomoPassEntitlements(revenuecat).grant(session, "o1", "user-1")
    assert revenuecat.calls == []
    assert session.events == ["rollback"]

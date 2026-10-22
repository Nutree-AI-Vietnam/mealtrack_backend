"""Granting MoMo passes through RevenueCat promotional entitlements.

The locked database steps are faked here and run against Postgres in
tests/integration/postgres/test_momo_pass_*.py.
"""

from datetime import timedelta

import pytest

from src.domain.utils.timezone_utils import utc_now
from src.infra.services import momo_pass_windows
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements
from src.infra.services.momo_pass_windows import GrantPlan

pytestmark = pytest.mark.asyncio

UID = "firebase-uid-1"
CHAIN_END = utc_now() + timedelta(weeks=8)
RESTACKED_END = CHAIN_END + timedelta(weeks=1)


class Steps:
    """Stands in for the locked database steps and records their order."""

    def __init__(self, reserved, restacked=None):
        self.reserved, self.restacked = reserved, restacked
        self.events: list[tuple] = []

    async def reserve(self, _db, order_id, user_id):
        self.events.append(("reserve", order_id, user_id))
        return self.reserved

    async def restack(self, _db, user_id, upstream_ends):
        self.events.append(("restack", user_id, upstream_ends))
        return self.restacked

    async def confirm_through(self, _db, user_id, uid, end):
        self.events.append(("confirm", user_id, uid, end))


class FakeRevenueCat:
    def __init__(self, steps, ok=True, promotional_ends=()):
        self.steps, self.ok, self.promotional_ends = steps, ok, promotional_ends

    async def promotional_expiries(self, app_user_id):
        self.steps.events.append(("read", app_user_id))
        return None if self.promotional_ends is None else list(self.promotional_ends)

    async def grant_promotional_entitlement(self, app_user_id, entitlement, end_ms):
        self.steps.events.append(("grant", app_user_id, entitlement, end_ms))
        return self.ok


@pytest.fixture
def steps(monkeypatch):
    def install(reserved, restacked=None):
        fake = Steps(reserved, restacked)
        for name in ("reserve", "restack", "confirm_through"):
            monkeypatch.setattr(momo_pass_windows, name, getattr(fake, name))
        return fake

    return install


def _ms(moment):
    return int(moment.timestamp() * 1000)


async def test_grant_sends_the_chain_end_then_confirms_what_it_covers(steps):
    fake = steps(GrantPlan(UID, send_end=CHAIN_END))

    granted = await MomoPassEntitlements(FakeRevenueCat(fake)).grant(
        None, "o1", "user-1"
    )

    assert granted
    assert fake.events == [
        ("reserve", "o1", "user-1"),
        ("grant", UID, "standard", _ms(CHAIN_END)),
        ("confirm", "user-1", UID, CHAIN_END),
    ]


async def test_failed_revenuecat_call_leaves_the_windows_for_a_retry(steps):
    fake = steps(GrantPlan(UID, send_end=CHAIN_END))

    granted = await MomoPassEntitlements(FakeRevenueCat(fake, ok=False)).grant(
        None, "o1", "user-1"
    )

    assert not granted
    assert [event[0] for event in fake.events] == ["reserve", "grant"]


async def test_granted_order_is_not_granted_again(steps):
    fake = steps(GrantPlan(UID, send_end=None))

    assert await MomoPassEntitlements(FakeRevenueCat(fake)).grant(None, "o1", "user-1")
    assert [event[0] for event in fake.events] == ["reserve"]


async def test_unpaid_order_is_never_granted(steps):
    fake = steps(None)

    assert not await MomoPassEntitlements(FakeRevenueCat(fake)).grant(
        None, "o1", "user-1"
    )
    assert [event[0] for event in fake.events] == ["reserve"]


async def test_lapsed_chain_is_settled_against_revenuecat_before_a_grant(steps):
    upstream_end = utc_now() + timedelta(days=3)
    fake = steps(
        GrantPlan(UID, send_end=CHAIN_END, lapsed=True),
        GrantPlan(UID, send_end=RESTACKED_END),
    )
    revenuecat = FakeRevenueCat(fake, promotional_ends=[upstream_end])

    assert await MomoPassEntitlements(revenuecat).grant(None, "o1", "user-1")

    assert fake.events == [
        ("reserve", "o1", "user-1"),
        ("read", UID),
        ("restack", "user-1", [upstream_end]),
        ("grant", UID, "standard", _ms(RESTACKED_END)),
        ("confirm", "user-1", UID, RESTACKED_END),
    ]


async def test_lapsed_chain_revenuecat_already_covers_needs_no_grant(steps):
    fake = steps(
        GrantPlan(UID, send_end=CHAIN_END, lapsed=True),
        GrantPlan(UID, send_end=None),
    )

    assert await MomoPassEntitlements(FakeRevenueCat(fake)).grant(None, "o1", "user-1")
    assert [event[0] for event in fake.events] == ["reserve", "read", "restack"]


async def test_lapsed_chain_waits_while_revenuecat_cannot_be_read(steps):
    fake = steps(GrantPlan(UID, send_end=CHAIN_END, lapsed=True))
    revenuecat = FakeRevenueCat(fake, promotional_ends=None)

    assert not await MomoPassEntitlements(revenuecat).grant(None, "o1", "user-1")
    assert [event[0] for event in fake.events] == ["reserve", "read"]

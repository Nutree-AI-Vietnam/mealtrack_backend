"""Syncing credited MoMo passes to RevenueCat promotional entitlements.

The database steps are faked here; crediting, locking and marking run against
Postgres in tests/integration/postgres/test_momo_pass_*.py.
"""

import logging
from datetime import timedelta

import pytest

from src.domain.utils.timezone_utils import utc_now
from src.infra.services import momo_pass_entitlement
from src.infra.services.momo_pass_entitlement import MomoPassEntitlements

pytestmark = pytest.mark.asyncio

UID = "firebase-uid-1"
LATEST_END = utc_now() + timedelta(weeks=8)


class Steps:
    """Stands in for the session and the database steps, in call order."""

    def __init__(self, unsent):
        self.unsent = unsent
        self.events: list[tuple] = []

    async def rollback(self):
        self.events.append(("rollback",))

    async def latest_unsent_end(self, _db, user_id):
        self.events.append(("read", user_id))
        return self.unsent

    async def mark_granted(self, _db, user_id, uid, end):
        self.events.append(("mark", user_id, uid, end))


class FakeRevenueCat:
    def __init__(self, steps, ok=True):
        self.steps, self.ok = steps, ok

    async def grant_promotional_entitlement(self, app_user_id, entitlement, end_ms):
        self.steps.events.append(("grant", app_user_id, entitlement, end_ms))
        return self.ok


@pytest.fixture
def steps(monkeypatch):
    def install(unsent):
        fake = Steps(unsent)
        monkeypatch.setattr(
            momo_pass_entitlement, "_latest_unsent_end", fake.latest_unsent_end
        )
        monkeypatch.setattr(momo_pass_entitlement, "_mark_granted", fake.mark_granted)
        return fake

    return install


def _ms(moment):
    return int(moment.timestamp() * 1000)


async def test_sync_sends_the_latest_end_outside_the_read_then_marks_it(steps):
    fake = steps((UID, LATEST_END))

    synced = await MomoPassEntitlements(FakeRevenueCat(fake)).sync(fake, "user-1")

    assert synced
    assert fake.events == [
        ("read", "user-1"),
        ("rollback",),
        ("grant", UID, "standard", _ms(LATEST_END)),
        ("mark", "user-1", UID, LATEST_END),
    ]


async def test_failed_revenuecat_call_leaves_the_credits_for_a_retry(steps):
    fake = steps((UID, LATEST_END))

    synced = await MomoPassEntitlements(FakeRevenueCat(fake, ok=False)).sync(
        fake, "user-1"
    )

    assert not synced
    assert [event[0] for event in fake.events] == ["read", "rollback", "grant"]


async def test_nothing_unsent_calls_nobody(steps):
    fake = steps(None)

    assert await MomoPassEntitlements(FakeRevenueCat(fake)).sync(fake, "user-1")
    assert [event[0] for event in fake.events] == ["read", "rollback"]


async def test_lapsed_credits_are_not_sent_and_are_marked_with_an_error(steps, caplog):
    ended = utc_now() - timedelta(minutes=1)
    fake = steps((UID, ended))

    with caplog.at_level(logging.ERROR, logger=momo_pass_entitlement.__name__):
        synced = await MomoPassEntitlements(FakeRevenueCat(fake)).sync(fake, "user-1")

    assert synced
    assert fake.events == [
        ("read", "user-1"),
        ("rollback",),
        ("mark", "user-1", UID, ended),
    ]
    assert "lapsed before they reached RevenueCat" in caplog.text

"""Syncing credited MoMo passes to RevenueCat promotional entitlements.

The database steps are faked here; crediting, locking and marking run against
Postgres in tests/integration/postgres/test_momo_pass_*.py.
"""

import asyncio
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

    def __init__(self, unsent, lock_free=True):
        self.unsent, self.lock_free = unsent, lock_free
        self.events: list[tuple] = []

    async def commit(self):
        self.events.append(("commit",))

    async def rollback(self):
        self.events.append(("rollback",))

    async def try_sync_lock(self, _db, user_id):
        self.events.append(("lock", user_id))
        return self.lock_free

    async def latest_unsent_end(self, _db, user_id):
        self.events.append(("read", user_id))
        return (UID, self.unsent) if self.unsent else None

    async def mark_granted(self, _db, user_id, uid, end):
        self.events.append(("mark", user_id, uid, end))
        if self.unsent and self.unsent <= end:
            self.unsent = None


class FakeRevenueCat:
    def __init__(self, steps, ok=True, credited_meanwhile=None, delay=0.0):
        self.steps, self.ok, self.delay = steps, ok, delay
        self.credited_meanwhile = credited_meanwhile

    async def grant_promotional_entitlement(self, app_user_id, entitlement, end_ms):
        self.steps.events.append(("grant", app_user_id, entitlement, end_ms))
        if self.credited_meanwhile:
            # Another request credits a later window while this grant runs.
            self.steps.unsent, self.credited_meanwhile = self.credited_meanwhile, None
        await asyncio.sleep(self.delay)
        return self.ok


@pytest.fixture
def steps(monkeypatch):
    def install(unsent, lock_free=True):
        fake = Steps(unsent, lock_free)
        for name, step in {
            "_latest_unsent_end": fake.latest_unsent_end,
            "_try_sync_lock": fake.try_sync_lock,
            "_mark_granted": fake.mark_granted,
        }.items():
            monkeypatch.setattr(momo_pass_entitlement, name, step)
        return fake

    return install


def _ms(moment):
    return int(moment.timestamp() * 1000)


async def test_sync_sends_the_latest_end_under_the_lock_then_marks_it(steps):
    fake = steps(LATEST_END)

    synced = await MomoPassEntitlements(FakeRevenueCat(fake)).sync(fake, "user-1")

    assert synced
    assert fake.events == [
        ("read", "user-1"),
        ("lock", "user-1"),
        ("read", "user-1"),
        ("grant", UID, "standard", _ms(LATEST_END)),
        ("mark", "user-1", UID, LATEST_END),
        ("commit",),
        ("read", "user-1"),
        ("rollback",),
    ]


async def test_credit_made_during_a_grant_is_sent_by_the_next_pass(steps):
    later_end = LATEST_END + timedelta(weeks=4)
    fake = steps(LATEST_END)
    revenuecat = FakeRevenueCat(fake, credited_meanwhile=later_end)

    assert await MomoPassEntitlements(revenuecat).sync(fake, "user-1")

    grants = [event[3] for event in fake.events if event[0] == "grant"]
    assert grants == [_ms(LATEST_END), _ms(later_end)]
    assert fake.unsent is None


async def test_failed_revenuecat_call_leaves_the_credits_for_a_retry(steps):
    fake = steps(LATEST_END)

    synced = await MomoPassEntitlements(FakeRevenueCat(fake, ok=False)).sync(
        fake, "user-1"
    )

    assert not synced
    assert [event[0] for event in fake.events] == [
        "read",
        "lock",
        "read",
        "grant",
        "rollback",
    ]


async def test_revenuecat_call_that_outlasts_the_timeout_is_a_failure(
    steps, monkeypatch
):
    monkeypatch.setattr(momo_pass_entitlement, "_GRANT_TIMEOUT_SECONDS", 0.01)
    fake = steps(LATEST_END)

    synced = await MomoPassEntitlements(FakeRevenueCat(fake, delay=1)).sync(
        fake, "user-1"
    )

    assert not synced
    assert [event[0] for event in fake.events][-2:] == ["grant", "rollback"]


async def test_request_that_finds_the_lock_taken_leaves_the_send_to_its_holder(
    steps,
):
    fake = steps(LATEST_END, lock_free=False)

    assert await MomoPassEntitlements(FakeRevenueCat(fake)).sync(fake, "user-1")
    assert [event[0] for event in fake.events] == ["read", "lock", "rollback"]


async def test_nothing_unsent_takes_no_lock_and_calls_nobody(steps):
    fake = steps(None)

    assert await MomoPassEntitlements(FakeRevenueCat(fake)).sync(fake, "user-1")
    assert [event[0] for event in fake.events] == ["read", "rollback"]


async def test_lapsed_credits_are_not_sent_and_are_marked_with_an_error(steps, caplog):
    ended = utc_now() - timedelta(minutes=1)
    fake = steps(ended)

    with caplog.at_level(logging.ERROR, logger=momo_pass_entitlement.__name__):
        synced = await MomoPassEntitlements(FakeRevenueCat(fake)).sync(fake, "user-1")

    assert synced
    assert [event[0] for event in fake.events] == [
        "read",
        "lock",
        "read",
        "mark",
        "commit",
        "read",
        "rollback",
    ]
    assert "lapsed before they reached RevenueCat" in caplog.text

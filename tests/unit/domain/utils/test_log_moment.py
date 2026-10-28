"""Logs for today keep their real time; logs for other days are stamped at local noon."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.domain.utils.timezone_utils import is_backdated_local, log_moment

# 2026-10-28 08:15 in Asia/Saigon.
NOW = datetime(2026, 10, 28, 1, 15, 30, 123456, tzinfo=UTC)


def test_no_date_returns_now():
    assert log_moment(None, "Asia/Saigon", NOW) == NOW


def test_today_returns_now():
    assert log_moment(date(2026, 10, 28), "Asia/Saigon", NOW) == NOW


def test_today_is_the_users_today_not_utcs():
    # 01:15 UTC is still 2026-10-27 in Los Angeles.
    assert log_moment(date(2026, 10, 27), "America/Los_Angeles", NOW) == NOW


@pytest.mark.parametrize("offset", [-3, -1, 1])
def test_other_days_return_local_noon(offset):
    day = date(2026, 10, 28) + timedelta(days=offset)
    stamped = log_moment(day, "Asia/Saigon", NOW)
    local = stamped.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    assert (local.date(), local.hour, local.minute) == (day, 12, 0)
    assert is_backdated_local(local)


@pytest.mark.parametrize(
    ("zone", "day"),
    [
        ("Asia/Saigon", date(2026, 10, 20)),
        ("America/Los_Angeles", date(2026, 10, 20)),
        # The day US clocks fall back.
        ("America/Los_Angeles", date(2026, 11, 1)),
    ],
)
def test_noon_stamp_reads_as_backdated_in_the_users_zone(zone, day):
    now = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)
    local = log_moment(day, zone, now).astimezone(
        ZoneInfo("Asia/Ho_Chi_Minh" if zone == "Asia/Saigon" else zone)
    )
    assert is_backdated_local(local)


def test_a_real_time_is_not_backdated():
    local = NOW.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    assert not is_backdated_local(local)
    assert not is_backdated_local(local.replace(hour=12, minute=0, second=1))

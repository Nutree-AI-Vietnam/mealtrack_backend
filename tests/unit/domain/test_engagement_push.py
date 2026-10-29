"""Send-time policy and planning for server-sent engagement pushes."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from src.domain.model.notification import (
    BILLING_ISSUE_PUSH_TTL,
    ENGAGEMENT_PUSH_ROUTES,
    EngagementPushKind,
    next_send_time,
    plan_billing_issue_push,
)

HCM = "Asia/Ho_Chi_Minh"  # UTC+7 all year
NEW_YORK = "America/New_York"  # EST/EDT; DST 2026-03-08 and 2026-11-01


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.unit
class TestNextSendTime:
    @pytest.mark.parametrize(
        "now",
        [
            _utc(2026, 10, 26, 2, 0),  # 09:00 local, the window opens
            _utc(2026, 10, 26, 6, 30),  # 13:30 local
            _utc(2026, 10, 26, 12, 59, 59),  # 19:59:59 local
        ],
        ids=["window-start", "midday", "last-second"],
    )
    def test_inside_the_local_window_sends_now(self, now):
        assert next_send_time(now, HCM) == now

    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            # 08:00 local -> 09:00 the same local day
            (_utc(2026, 10, 26, 1, 0), _utc(2026, 10, 26, 2, 0)),
            # 20:00 local, the window has closed -> 09:00 the next day
            (_utc(2026, 10, 26, 13, 0), _utc(2026, 10, 27, 2, 0)),
            # 22:00 local
            (_utc(2026, 10, 26, 15, 0), _utc(2026, 10, 27, 2, 0)),
            # 00:30 local on the 27th, still the 26th in UTC
            (_utc(2026, 10, 26, 17, 30), _utc(2026, 10, 27, 2, 0)),
        ],
        ids=["early-morning", "window-end", "late-evening", "after-local-midnight"],
    )
    def test_outside_the_window_waits_for_the_next_local_nine_am(self, now, expected):
        assert next_send_time(now, HCM) == expected

    def test_new_york_inside_the_window_sends_now(self):
        now = _utc(2026, 10, 26, 16, 0)  # 12:00 EDT

        assert next_send_time(now, NEW_YORK) == now

    def test_spring_forward_uses_the_new_offset(self):
        # Sat 2026-03-07 21:00 EST; the clocks go forward that night.
        now = _utc(2026, 3, 8, 2, 0)

        # Sun 09:00 EDT (UTC-4), not 09:00 EST (UTC-5).
        assert next_send_time(now, NEW_YORK) == _utc(2026, 3, 8, 13, 0)

    def test_fall_back_uses_the_new_offset(self):
        # Sat 2026-10-31 22:00 EDT; the clocks go back that night.
        now = _utc(2026, 11, 1, 2, 0)

        # Sun 09:00 EST (UTC-5), not 09:00 EDT (UTC-4).
        assert next_send_time(now, NEW_YORK) == _utc(2026, 11, 1, 14, 0)

    def test_before_dawn_on_the_dst_day_targets_that_morning(self):
        # 01:30 EST, half an hour before the switch.
        now = _utc(2026, 3, 8, 6, 30)

        assert next_send_time(now, NEW_YORK) == _utc(2026, 3, 8, 13, 0)

    @pytest.mark.parametrize("timezone", [None, "", "Mars/Olympus_Mons", "UTC"])
    def test_missing_or_unknown_zone_counts_as_utc(self, timezone):
        assert next_send_time(_utc(2026, 10, 26, 21, 0), timezone) == _utc(
            2026, 10, 27, 9, 0
        )

    def test_legacy_zone_alias_is_resolved(self):
        assert next_send_time(_utc(2026, 10, 26, 15, 0), "Asia/Saigon") == _utc(
            2026, 10, 27, 2, 0
        )

    def test_naive_now_is_taken_as_utc(self):
        naive = datetime(2026, 10, 26, 15, 0)

        assert next_send_time(naive, HCM) == _utc(2026, 10, 27, 2, 0)

    def test_result_is_in_utc(self):
        sent_at = next_send_time(_utc(2026, 10, 26, 15, 0), NEW_YORK)

        assert sent_at.utcoffset() == timedelta(0)


@pytest.mark.unit
class TestPlanBillingIssuePush:
    def test_outside_the_window_it_is_due_at_the_next_local_nine_am(self):
        push = plan_billing_issue_push(
            user_id="user-1",
            timezone=HCM,
            language="vi",
            now=_utc(2026, 10, 26, 15, 0),  # 22:00 local
        )

        assert push.kind is EngagementPushKind.BILLING_ISSUE
        assert push.user_id == "user-1"
        assert push.scheduled_for_utc == _utc(2026, 10, 27, 2, 0)
        assert push.scheduled_date == date(2026, 10, 27)
        assert push.expires_at == _utc(2026, 10, 29, 2, 0)
        assert push.context == {"kind": "billing_issue", "language": "vi"}
        assert str(UUID(push.id)) == push.id

    def test_scheduled_date_is_the_users_local_date(self):
        # 18:00 on the 26th in Los Angeles is already the 27th in UTC.
        now = _utc(2026, 10, 27, 1, 0)

        push = plan_billing_issue_push(
            user_id="user-1", timezone="America/Los_Angeles", language="en", now=now
        )

        assert push.scheduled_for_utc == now
        assert push.scheduled_date == date(2026, 10, 26)

    def test_it_expires_48_hours_after_it_is_due(self):
        push = plan_billing_issue_push(
            user_id="user-1",
            timezone=HCM,
            language="en",
            now=_utc(2026, 10, 26, 3, 0),
        )

        assert BILLING_ISSUE_PUSH_TTL == timedelta(hours=48)
        assert push.expires_at - push.scheduled_for_utc == BILLING_ISSUE_PUSH_TTL

    @pytest.mark.parametrize(
        ("language", "stored"),
        [
            ("vi-VN", "vi"),
            ("ZH", "zh"),
            ("ja", "ja"),
            ("ko", "en"),
            (None, "en"),
            ("", "en"),
        ],
    )
    def test_language_is_stored_as_an_app_locale(self, language, stored):
        push = plan_billing_issue_push(
            user_id="user-1",
            timezone=None,
            language=language,
            now=_utc(2026, 10, 26, 12, 0),
        )

        assert push.context == {"kind": "billing_issue", "language": stored}

    def test_each_plan_gets_its_own_id(self):
        now = _utc(2026, 10, 26, 12, 0)

        first = plan_billing_issue_push(
            user_id="user-1", timezone=None, language="en", now=now
        )
        second = plan_billing_issue_push(
            user_id="user-1", timezone=None, language="en", now=now
        )

        assert first.id != second.id
        assert (first.scheduled_date, first.scheduled_for_utc) == (
            second.scheduled_date,
            second.scheduled_for_utc,
        )


@pytest.mark.unit
def test_every_kind_opens_a_screen():
    assert set(ENGAGEMENT_PUSH_ROUTES) == set(EngagementPushKind)
    assert ENGAGEMENT_PUSH_ROUTES[EngagementPushKind.BILLING_ISSUE] == "/profile"

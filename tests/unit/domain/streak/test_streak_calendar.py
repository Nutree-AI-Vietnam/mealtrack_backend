"""The streak on a calendar: logged days count, restored days only bridge."""

from datetime import date, timedelta

from src.domain.model.streak import build_streak_calendar

TODAY = date(2026, 10, 28)
LAUNCH = date(2026, 10, 1)


def _days(last: date, count: int) -> list[date]:
    return [last - timedelta(days=offset) for offset in range(count)]


def _calendar(logged, *, restored=(), today=TODAY, same_day_from=None, backdated=()):
    log_days = [(day, True) for day in logged] + [(day, False) for day in backdated]
    return build_streak_calendar(log_days, restored, today, same_day_from)


def test_no_logs():
    calendar = _calendar([])
    assert (calendar.current_streak, calendar.best_streak) == (0, 0)
    assert calendar.last_logged_date is None
    assert calendar.offer is None
    assert calendar.restored_recent == ()


def test_today_only():
    calendar = _calendar([TODAY])
    assert (calendar.current_streak, calendar.best_streak) == (1, 1)
    assert calendar.last_logged_date == TODAY


def test_yesterday_only_keeps_the_streak_until_today_ends():
    calendar = _calendar([TODAY - timedelta(days=1)])
    assert calendar.current_streak == 1
    assert calendar.offer is None


def test_one_missed_day_after_twelve_is_offered():
    missed = TODAY - timedelta(days=1)
    calendar = _calendar(_days(missed - timedelta(days=1), 12))
    assert calendar.current_streak == 0
    assert calendar.best_streak == 12
    assert calendar.offer is not None
    assert calendar.offer.missed_dates == (missed,)
    assert calendar.offer.restorable_until == missed + timedelta(days=2)
    assert calendar.offer.broken_streak == 12


def test_logging_today_keeps_the_offer():
    missed = TODAY - timedelta(days=1)
    calendar = _calendar([TODAY, *_days(missed - timedelta(days=1), 12)])
    assert calendar.current_streak == 1
    assert calendar.offer is not None
    assert calendar.offer.missed_dates == (missed,)


def test_a_log_the_day_after_the_miss_does_not_close_the_offer():
    missed = TODAY - timedelta(days=2)
    calendar = _calendar(
        [missed + timedelta(days=1), *_days(missed - timedelta(days=1), 5)]
    )
    assert calendar.current_streak == 1
    assert calendar.offer is not None
    assert calendar.offer.missed_dates == (missed,)
    assert calendar.offer.restorable_until == TODAY


def test_the_offer_closes_two_days_after_the_miss():
    missed = TODAY - timedelta(days=3)
    for logged_after in (
        [missed + timedelta(days=1)],
        [missed + timedelta(days=1), missed + timedelta(days=2)],
    ):
        calendar = _calendar([*logged_after, *_days(missed - timedelta(days=1), 5)])
        assert calendar.offer is None


def test_two_missed_days_are_offered_together():
    first = TODAY - timedelta(days=2)
    calendar = _calendar(_days(first - timedelta(days=1), 4))
    assert calendar.offer is not None
    assert calendar.offer.missed_dates == (first, first + timedelta(days=1))
    assert calendar.offer.restorable_until == TODAY


def test_three_missed_days_are_not_offered():
    calendar = _calendar(_days(TODAY - timedelta(days=4), 10))
    assert calendar.offer is None


def test_a_broken_run_of_two_is_not_offered():
    calendar = _calendar(_days(TODAY - timedelta(days=2), 2))
    assert calendar.offer is None


def test_a_broken_run_of_exactly_three_is_offered():
    calendar = _calendar(_days(TODAY - timedelta(days=2), 3))
    assert calendar.offer is not None
    assert calendar.offer.broken_streak == 3


def test_a_restored_yesterday_bridges_without_counting():
    yesterday = TODAY - timedelta(days=1)
    run = _days(yesterday - timedelta(days=1), 12)
    unlogged_today = _calendar(run, restored=[yesterday])
    logged_today = _calendar([TODAY, *run], restored=[yesterday])
    assert unlogged_today.current_streak == 12
    assert logged_today.current_streak == 13
    assert unlogged_today.offer is None
    assert unlogged_today.restored_recent == (yesterday,)


def test_two_restored_days_bridge():
    first = TODAY - timedelta(days=2)
    restored = [first, first + timedelta(days=1)]
    calendar = _calendar(
        [TODAY, *_days(first - timedelta(days=1), 6)], restored=restored
    )
    assert calendar.current_streak == 7
    assert calendar.offer is None


def test_best_streak_merges_bridged_runs_without_counting_restored_days():
    restored_day = TODAY - timedelta(days=6)
    calendar = _calendar(
        [
            *_days(TODAY - timedelta(days=1), 5),
            *_days(restored_day - timedelta(days=1), 10),
        ],
        restored=[restored_day],
    )
    assert calendar.best_streak == 15
    assert calendar.current_streak == 15


def test_a_backdated_only_day_after_launch_stays_missed():
    missed = TODAY - timedelta(days=1)
    calendar = _calendar(
        _days(missed - timedelta(days=1), 12), backdated=[missed], same_day_from=LAUNCH
    )
    assert calendar.current_streak == 0
    assert calendar.offer is not None
    assert calendar.offer.missed_dates == (missed,)
    assert calendar.last_logged_date == missed - timedelta(days=1)


def test_a_backdated_day_before_launch_counts():
    missed = TODAY - timedelta(days=1)
    calendar = _calendar(
        _days(missed - timedelta(days=1), 12),
        backdated=[missed],
        same_day_from=TODAY,
    )
    assert calendar.current_streak == 13
    assert calendar.offer is None


def test_without_a_launch_date_every_logged_day_counts():
    missed = TODAY - timedelta(days=1)
    calendar = _calendar(_days(missed - timedelta(days=1), 12), backdated=[missed])
    assert calendar.current_streak == 13
    assert calendar.last_logged_date == missed


def test_a_day_with_a_same_day_and_a_backdated_log_counts():
    yesterday = TODAY - timedelta(days=1)
    calendar = _calendar(
        _days(yesterday, 4), backdated=[yesterday], same_day_from=LAUNCH
    )
    assert calendar.current_streak == 4


def test_a_backdated_log_on_a_restored_day_leaves_it_restored():
    yesterday = TODAY - timedelta(days=1)
    calendar = _calendar(
        _days(yesterday - timedelta(days=1), 12),
        restored=[yesterday],
        backdated=[yesterday],
        same_day_from=LAUNCH,
    )
    assert calendar.current_streak == 12
    assert calendar.restored_recent == (yesterday,)
    assert calendar.last_logged_date == yesterday - timedelta(days=1)


def test_last_logged_date_is_never_a_restored_day():
    yesterday = TODAY - timedelta(days=1)
    calendar = _calendar(_days(yesterday - timedelta(days=1), 5), restored=[yesterday])
    assert calendar.last_logged_date == yesterday - timedelta(days=1)


def test_restored_days_older_than_two_weeks_are_not_listed():
    old, recent = TODAY - timedelta(days=14), TODAY - timedelta(days=13)
    calendar = _calendar([], restored=[old, recent])
    assert calendar.restored_recent == (recent,)


def test_offer_window_crosses_a_month_and_a_year():
    today = date(2027, 1, 1)
    missed = date(2026, 12, 30)
    calendar = _calendar(
        [date(2026, 12, 31), *_days(date(2026, 12, 29), 3)], today=today
    )
    assert calendar.offer is not None
    assert calendar.offer.missed_dates == (missed,)
    assert calendar.offer.restorable_until == today
    assert calendar.current_streak == 1


def test_without_restores_or_a_launch_date_the_numbers_match_plain_runs():
    logged = [
        *_days(TODAY, 3),
        *_days(TODAY - timedelta(days=5), 7),
        TODAY - timedelta(days=20),
    ]
    calendar = _calendar(logged)
    assert calendar.current_streak == 3
    assert calendar.best_streak == 7
    assert calendar.last_logged_date == TODAY


def test_a_streak_longer_than_a_year_counts_in_full():
    """A lookback shorter than the run would clip current and best."""
    logged = _days(TODAY, 400)
    calendar = _calendar(logged)
    assert calendar.current_streak == 400
    assert calendar.best_streak == 400
    assert calendar.last_logged_date == TODAY
    assert calendar.offer is None


def test_a_gap_keeps_an_older_best_streak():
    """best_streak is the longest run ever, including one that ended long ago."""
    current = _days(TODAY, 4)
    older = _days(TODAY - timedelta(days=200), 50)
    calendar = _calendar([*current, *older])
    assert calendar.current_streak == 4
    assert calendar.best_streak == 50
    assert calendar.last_logged_date == TODAY
    assert calendar.offer is None


def test_a_restored_day_bridges_an_old_run_into_best_streak():
    """A restore far from today still joins the runs on either side of it."""
    older_end = TODAY - timedelta(days=200)
    bridge = older_end + timedelta(days=1)
    calendar = _calendar(
        [
            *_days(TODAY, 2),
            *_days(older_end, 15),
            *_days(bridge + timedelta(days=10), 10),
        ],
        restored=[bridge],
    )
    assert calendar.current_streak == 2
    assert calendar.best_streak == 25
    assert calendar.restored_recent == ()
    assert calendar.last_logged_date == TODAY


def test_a_recent_restore_still_lists_when_the_logged_run_is_old():
    recent = TODAY - timedelta(days=2)
    calendar = _calendar(
        _days(TODAY - timedelta(days=40), 12),
        restored=[recent],
    )
    assert calendar.current_streak == 0
    assert calendar.best_streak == 12
    assert calendar.restored_recent == (recent,)
    assert calendar.last_logged_date == TODAY - timedelta(days=40)


def test_same_local_dates_do_not_depend_on_a_zone_once_they_are_days():
    """Day identity is a calendar date. The zone is applied before this."""
    logged = [date(2026, 3, 8), date(2026, 3, 9)]
    utc = _calendar(logged, today=date(2026, 3, 9))
    # A meal at 2026-03-08 17:00 UTC is 2026-03-09 in Tokyo and 2026-03-08
    # in Los Angeles. Those are different days; once resolved, a one-day
    # gap is a gap in every zone.
    split = _calendar([date(2026, 3, 8)], today=date(2026, 3, 9))
    assert (utc.current_streak, utc.best_streak) == (2, 2)
    assert (split.current_streak, split.best_streak) == (1, 1)
    assert split.offer is None

from datetime import date, datetime

from src.domain.model.vacation.vacation import Vacation


def _vacation(start: date, end: date) -> Vacation:
    return Vacation(
        vacation_id="v1",
        user_id="u1",
        start_date=start,
        end_date=end,
        ended_on=None,
        frozen_calories=1800,
        frozen_protein=140,
        frozen_carbs=180,
        frozen_fat=60,
        created_at=datetime(2026, 10, 1),
    )


def test_cancel_on_the_only_day_removes_the_break():
    vacation = _vacation(date(2026, 10, 7), date(2026, 10, 7))

    assert vacation.drop_today(date(2026, 10, 7)) is True


def test_cancel_before_the_break_removes_it():
    vacation = _vacation(date(2026, 10, 12), date(2026, 10, 18))

    assert vacation.drop_today(date(2026, 10, 7)) is True


def test_cancel_after_earlier_days_stops_before_today():
    vacation = _vacation(date(2026, 10, 5), date(2026, 10, 10))

    assert vacation.drop_today(date(2026, 10, 7)) is False
    assert vacation.effective_end == date(2026, 10, 6)

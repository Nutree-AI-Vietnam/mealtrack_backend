"""Tests for progress summary date-window clamping."""

from datetime import date

from src.domain.services.progress_summary_window import (
    PROGRESS_SUMMARY_CAP_DAYS,
    clamp_progress_window,
    logged_status_for_meal_count,
)


def test_defaults_to_created_at_through_today():
    start, end = clamp_progress_window(
        None,
        None,
        created_on=date(2026, 3, 1),
        today=date(2026, 9, 1),
    )
    assert start == date(2026, 3, 1)
    assert end == date(2026, 9, 1)


def test_clamps_pre_created_at_start():
    start, end = clamp_progress_window(
        date(2025, 1, 1),
        date(2026, 9, 1),
        created_on=date(2026, 3, 1),
        today=date(2026, 9, 1),
    )
    assert start == date(2026, 3, 1)
    assert end == date(2026, 9, 1)


def test_clamps_future_end_to_today():
    start, end = clamp_progress_window(
        date(2026, 8, 1),
        date(2026, 12, 1),
        created_on=date(2026, 1, 1),
        today=date(2026, 9, 1),
    )
    assert end == date(2026, 9, 1)
    assert start == date(2026, 8, 1)


def test_clamps_oversize_range_to_most_recent_cap_days():
    start, end = clamp_progress_window(
        date(2024, 1, 1),
        date(2026, 9, 1),
        created_on=date(2024, 1, 1),
        today=date(2026, 9, 1),
    )
    assert end == date(2026, 9, 1)
    assert (end - start).days + 1 == PROGRESS_SUMMARY_CAP_DAYS


def test_logged_status_mapping():
    assert logged_status_for_meal_count(0) == "none"
    assert logged_status_for_meal_count(1) == "partial"
    assert logged_status_for_meal_count(2) == "full"
    assert logged_status_for_meal_count(5) == "full"

"""Inclusive date-window clamping for GET /v1/progress/summary."""

from __future__ import annotations

from datetime import date, timedelta

PROGRESS_SUMMARY_CAP_DAYS = 400


def clamp_progress_window(
    requested_start: date | None,
    requested_end: date | None,
    *,
    created_on: date,
    today: date,
    cap_days: int = PROGRESS_SUMMARY_CAP_DAYS,
) -> tuple[date, date]:
    """Clamp to [created_on, today] and the most recent `cap_days` inclusive days.

    Caller must 422 inverted client ranges before calling this. After clamp,
    start is always <= end (clock-skew created_on > today collapses to today).
    """
    end = min(requested_end or today, today)
    start = max(requested_start or created_on, created_on)
    if start > end:
        start = end
    inclusive_days = (end - start).days + 1
    if inclusive_days > cap_days:
        start = max(end - timedelta(days=cap_days - 1), created_on)
    return start, end


def logged_status_for_meal_count(meal_count: int) -> str:
    """Map meal count to the summary logged_status enum."""
    if meal_count <= 0:
        return "none"
    if meal_count == 1:
        return "partial"
    return "full"

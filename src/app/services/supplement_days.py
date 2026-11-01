"""Supplement doses as logged days: the streak, the weekly recap and the day feed.

Every helper returns nothing while the supplement tables are not migrated, so
a deploy that runs before its migration keeps these hot reads working.
"""

from __future__ import annotations

from datetime import date, datetime, tzinfo
from typing import Any

from src.app.services.supplement_payloads import dose_activity


async def _repo(uow: Any) -> Any | None:
    repo = getattr(uow, "supplements", None)
    # Only a definite yes counts; anything else reads as "not migrated yet".
    if repo is None or await repo.schema_ready() is not True:
        return None
    return repo


def dose_day(taken_at: datetime, tz: tzinfo, now: datetime) -> tuple[date, bool]:
    """The dose's local day, and whether it was logged on that day itself."""
    local_date = taken_at.astimezone(tz).date()
    return local_date, local_date == now.astimezone(tz).date()


async def with_dose_days(
    uow: Any, user_id: str, log_days: list[tuple[date, bool]]
) -> list[tuple[date, bool]]:
    """Meal log days plus dose days, newest first; a day is "on the day" if any log was."""
    repo = await _repo(uow)
    if repo is None:
        return log_days
    merged = dict(log_days)
    for day, on_day in await repo.log_days(user_id):
        merged[day] = merged.get(day, False) or on_day
    return sorted(merged.items(), reverse=True)


async def with_dose_dates(uow: Any, user_id: str, dates: list[date]) -> list[date]:
    """Days with a meal or a dose, newest first."""
    repo = await _repo(uow)
    if repo is None:
        return dates
    days = {day for day, _ in await repo.log_days(user_id)}
    return sorted(days.union(dates), reverse=True)


async def dose_activities(uow: Any, user_id: str, local_date: date) -> list[dict]:
    """The day's doses as activity rows."""
    repo = await _repo(uow)
    if repo is None:
        return []
    doses = await repo.intakes_between(user_id, local_date, local_date)
    products = await repo.products_for(user_id, (dose.product_id for dose in doses))
    missing = {dose.product_id for dose in doses} - products.keys()
    if missing:
        # The feed is all or nothing: a 200 is taken as the whole day.
        raise LookupError(f"supplement products failed to load: {sorted(missing)}")
    return [dose_activity(dose, products[dose.product_id]) for dose in doses]

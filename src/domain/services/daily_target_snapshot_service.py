"""Write-once snapshots of today's adjusted daily calorie target."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from src.domain.utils.timezone_utils import get_zone_info

logger = logging.getLogger(__name__)


async def persist_today_snapshot(
    uow: Any,
    user_id: str,
    target_date: date,
    adjusted_calories: float,
    user_timezone: str,
    *,
    replace: bool = False,
) -> None:
    """Write today's target. Other dates stay untouched.

    The default insert is write-once. A pace change passes replace=True so
    today's stored target matches the new base. Earlier dates are not rewritten.
    """
    today = datetime.now(get_zone_info(user_timezone)).date()
    if target_date != today:
        return
    repo = getattr(uow, "daily_target_snapshots", None)
    if repo is None:
        return
    try:
        if replace:
            await repo.replace_calories(user_id, target_date, adjusted_calories)
        else:
            await repo.insert_if_absent(user_id, target_date, adjusted_calories)
    except Exception:
        logger.exception(
            "Failed to persist daily target snapshot for user %s on %s",
            user_id,
            target_date,
        )


async def forget_today_snapshot(uow: Any, user_id: str, today: date) -> None:
    """Drop today's target after a setting changes how targets are computed.

    The next read stores today's target again under the new setting. Earlier
    dates keep the target that applied when they were stored.
    """
    repo = getattr(uow, "daily_target_snapshots", None)
    if repo is None:
        return
    await repo.delete_for_date(user_id, today)


async def maybe_write_today_snapshot(
    uow: Any,
    user_id: str,
    target_date: date,
    adjusted_calories: float,
    user_timezone: str,
) -> None:
    """Insert today's adjusted target if absent. Never updates, never backfills."""
    await persist_today_snapshot(
        uow, user_id, target_date, adjusted_calories, user_timezone
    )

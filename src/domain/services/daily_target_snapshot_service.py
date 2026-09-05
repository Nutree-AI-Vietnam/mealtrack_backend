"""Write-once snapshots of today's adjusted daily calorie target."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from src.domain.utils.timezone_utils import get_zone_info

logger = logging.getLogger(__name__)


async def maybe_write_today_snapshot(
    uow: Any,
    user_id: str,
    target_date: date,
    adjusted_calories: float,
    user_timezone: str,
) -> None:
    """Insert today's adjusted target if absent. Never updates, never backfills."""
    today = datetime.now(get_zone_info(user_timezone)).date()
    if target_date != today:
        return
    repo = getattr(uow, "daily_target_snapshots", None)
    if repo is None:
        return
    try:
        await repo.insert_if_absent(user_id, target_date, adjusted_calories)
    except Exception:
        logger.exception(
            "Failed to persist daily target snapshot for user %s on %s",
            user_id,
            target_date,
        )

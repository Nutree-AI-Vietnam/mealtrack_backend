"""Shared vacation payload and cache invalidation."""

from __future__ import annotations

import logging
from datetime import date

from src.domain.cache.cache_keys import CacheKeys
from src.domain.model.vacation.vacation import Vacation
from src.domain.ports.cache_port import CachePort
from src.domain.services.vacation_rules import status_for, to_window

logger = logging.getLogger(__name__)


def vacation_payload(vacation: Vacation, today: date) -> dict:
    window = to_window(vacation)
    return {
        "id": vacation.vacation_id,
        "start_date": vacation.start_date.isoformat(),
        "end_date": window.end.isoformat(),
        "scheduled_end_date": vacation.end_date.isoformat(),
        "status": status_for(window, today),
        "frozen_calories": vacation.frozen_calories,
        "frozen_protein": vacation.frozen_protein,
        "frozen_carbs": vacation.frozen_carbs,
        "frozen_fat": vacation.frozen_fat,
    }


async def invalidate_vacation_views(cache: CachePort | None, user_id: str) -> None:
    if cache is None:
        return
    streak_key, _ = CacheKeys.user_streak(user_id)
    try:
        await cache.invalidate(streak_key)
        await cache.invalidate_pattern(f"user:{user_id}:macros:*")
    except Exception:
        logger.warning("Failed to invalidate vacation caches for %s", user_id)

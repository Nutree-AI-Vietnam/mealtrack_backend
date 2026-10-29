"""Load personal Fix-result corrections as short vision prompt hints."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from src.domain.model.meal.scan_correction_memory import (
    ScanCorrectionMemory,
    extract_fix_result_corrections,
    rank_scan_corrections,
)
from src.domain.ports.async_unit_of_work_port import AsyncUnitOfWorkPort

logger = logging.getLogger(__name__)

# Keep scan hot path snappy; memory is best-effort only.
_DEFAULT_TIMEOUT_S = 0.05
_MEAL_LIMIT = 30
_HINT_LIMIT = 3


async def load_correction_hints_for_scan(
    *,
    user_id: str | None,
    user_description: str | None = None,
    uow_factory: Callable[[], AsyncUnitOfWorkPort] | None = None,
    meals_repo: Any | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[str]:
    """Return up to 3 hint lines for this user, or [] on any failure/timeout."""
    if not user_id or (uow_factory is None and meals_repo is None):
        return []

    try:
        return await asyncio.wait_for(
            _load(
                user_id=user_id,
                user_description=user_description,
                uow_factory=uow_factory,
                meals_repo=meals_repo,
            ),
            timeout=timeout_s,
        )
    except Exception as exc:
        logger.info(
            "scan_correction_hints_skipped user_id=%s err=%s",
            user_id,
            exc,
        )
        return []


async def _load(
    *,
    user_id: str,
    user_description: str | None,
    uow_factory: Callable[[], AsyncUnitOfWorkPort] | None,
    meals_repo: Any | None,
) -> list[str]:
    if meals_repo is not None:
        rows = await meals_repo.list_recent_fix_result_raw_payloads(
            user_id,
            limit=_MEAL_LIMIT,
        )
    else:
        assert uow_factory is not None
        async with uow_factory() as uow:
            rows = await uow.meals.list_recent_fix_result_raw_payloads(
                user_id,
                limit=_MEAL_LIMIT,
            )

    memories: list[ScanCorrectionMemory] = []
    for meal_id, dish_name, raw in rows:
        # Newest meals first from SQL; reverse each meal's history so the
        # latest correction on that meal ranks ahead of older ones.
        meal_memories = extract_fix_result_corrections(
            raw,
            meal_id=meal_id,
            dish_name=dish_name,
        )
        memories.extend(reversed(meal_memories))

    ranked = rank_scan_corrections(
        memories,
        user_description=user_description,
        limit=_HINT_LIMIT,
    )
    return [memory.hint_line() for memory in ranked]

"""Response schema for restoring missed streak days."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.api.schemas.progress_schemas import StreakResponse


class StreakRestoreResponse(BaseModel):
    # restored: the days are restored (or already were, by this user).
    # credited_only: a bought pack was credited but nothing was spent.
    outcome: Literal["restored", "credited_only"]
    restored_dates: list[str]  # YYYY-MM-DD
    credited: int  # restores in the verified pack, 0 without a purchase
    streak: StreakResponse

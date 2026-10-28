"""Server switches for streak restores."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StreakRestorePolicy:
    production: bool = False
    # Internal accounts (App Review, QA) whose sandbox purchases credit in production.
    sandbox_user_ids: frozenset[str] = frozenset()
    spend_enabled: bool = True
    claim_enabled: bool = True
    # From this local date on, only logs made on the day itself keep a streak.
    same_day_logs_from: date | None = None

    def allows_sandbox(self, user_id: str) -> bool:
        """Whether a sandbox purchase may credit this user."""
        return not self.production or user_id in self.sandbox_user_ids

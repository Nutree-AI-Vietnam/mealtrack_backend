"""A stored weekly recap: the week's stats snapshot and the rendered story."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass
class WeeklyRecap:
    """One user's recap for one Monday-to-Sunday week. Written once.

    ``stats`` is the WeekStats snapshot (see ``week_stats_codec``) so the
    recap can be re-rendered in another language with the same numbers.
    ``payload`` is the story in ``locale``, exactly as the app receives it.
    """

    user_id: str
    week_start: date
    case_id: str
    locale: str
    stats: dict[str, Any]
    payload: dict[str, Any]
    created_at: datetime | None = None

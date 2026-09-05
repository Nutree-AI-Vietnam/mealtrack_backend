"""Query to read leftover-split daily adjustment preference."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetWeeklyAutoAdjustQuery(Query):
    """Load whether leftover calories auto-adjust daily targets."""

    user_id: str

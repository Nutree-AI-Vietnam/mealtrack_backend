"""Query for per-day progress summary over an inclusive date range."""

from dataclasses import dataclass
from datetime import date

from src.app.events.base import Query


@dataclass
class GetProgressSummaryQuery(Query):
    user_id: str
    start_date: date | None = None
    end_date: date | None = None
    header_timezone: str | None = None

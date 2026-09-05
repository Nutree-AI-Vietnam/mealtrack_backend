"""Query to retrieve distinct recent meals within the last 7 calendar days."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetRecentMealsQuery(Query):
    user_id: str
    user_timezone: str | None = None
    limit: int = 10
    language: str = "en"

"""Query to retrieve unique recent meals within the last 30 calendar days."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetRecentMealsQuery(Query):
    user_id: str
    user_timezone: str | None = None
    limit: int = 20
    language: str = "en"

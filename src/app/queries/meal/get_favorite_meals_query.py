"""Query to retrieve user's favorite meals ordered newest-favorited first."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetFavoriteMealsQuery(Query):
    user_id: str
    limit: int = 50
    language: str = "en"

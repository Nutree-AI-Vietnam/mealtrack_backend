"""Query to read the activity calories preference."""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetActivityCaloriesQuery(Query):
    """Load whether calories burned in logged movement raise the food budget."""

    user_id: str

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class GetWeeklyGroceriesQuery(Query):
    user_id: str
    plan_id: str

from dataclasses import dataclass
from datetime import date

from src.app.events.base import Query


@dataclass
class GetCurrentWeeklyPlanQuery(Query):
    user_id: str
    week_start_date: date

"""A dated pause that holds streak and targets still."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Vacation:
    vacation_id: str
    user_id: str
    start_date: date
    end_date: date
    ended_on: date | None
    frozen_calories: float
    frozen_protein: float
    frozen_carbs: float
    frozen_fat: float
    created_at: datetime

    @property
    def effective_end(self) -> date:
        if self.ended_on is not None and self.ended_on < self.end_date:
            return self.ended_on
        return self.end_date

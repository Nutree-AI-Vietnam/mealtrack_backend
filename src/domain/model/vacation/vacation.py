"""A dated pause that holds streak and targets still."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta


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

    def drop_today(self, today: date) -> bool:
        """Take today out of the break.

        A break that has not already covered an earlier day is removed.
        One that has keeps those earlier days and stops before today.
        Returns True when the row should be deleted.
        """
        if today <= self.start_date:
            return True
        self.ended_on = today - timedelta(days=1)
        return False

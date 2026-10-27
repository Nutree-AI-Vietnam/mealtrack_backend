"""Command to set a vacation with a first day and a last day."""

from dataclasses import dataclass
from datetime import date


@dataclass
class SetVacationCommand:
    user_id: str
    start_date: date
    end_date: date

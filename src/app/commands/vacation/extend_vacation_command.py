"""Command to move an open vacation's last day later."""

from dataclasses import dataclass
from datetime import date


@dataclass
class ExtendVacationCommand:
    user_id: str
    end_date: date

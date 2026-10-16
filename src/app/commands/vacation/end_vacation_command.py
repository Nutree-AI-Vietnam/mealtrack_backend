"""Command to end an open vacation on the user's today."""

from dataclasses import dataclass


@dataclass
class EndVacationCommand:
    user_id: str

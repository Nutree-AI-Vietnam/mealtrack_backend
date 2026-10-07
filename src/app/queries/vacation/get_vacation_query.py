"""Query for the user's open vacation, or the one that ended yesterday."""

from dataclasses import dataclass


@dataclass
class GetVacationQuery:
    user_id: str

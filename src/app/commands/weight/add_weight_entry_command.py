"""Command to add a weight entry."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class AddWeightEntryCommand:
    """Add a single weight entry."""

    user_id: str
    weight_kg: float
    recorded_at: datetime
    # Read a recorded_at without an offset in this zone when the user has none.
    header_timezone: str | None = None

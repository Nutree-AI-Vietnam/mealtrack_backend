"""Command to sync weight entries from mobile."""

from dataclasses import dataclass
from datetime import datetime
from typing import List


@dataclass
class WeightEntryData:
    """Single weight entry for sync."""

    weight_kg: float
    recorded_at: datetime


@dataclass
class SyncWeightEntriesCommand:
    """Bulk sync weight entries from mobile."""

    user_id: str
    entries: List[WeightEntryData]
    # Read a recorded_at without an offset in this zone when the user has none.
    header_timezone: str | None = None

"""Turn the model's activity list into entries priced like the app's detail screen.

Listed activities take the catalog MET for their intensity and the app's
preset minutes; unlisted ones take the model's MET after it is clamped here.
Bad items are dropped, never the whole sentence.
"""

from dataclasses import dataclass
from typing import Any

from src.app.services.describe_log_parser import (
    DESCRIBE_MAX_ITEMS,
    clamp,
    finite,
    short_name,
)
from src.domain.model.movement import MovementIntensity
from src.domain.services.movement_catalog_service import (
    default_duration_min,
    get_activity,
    get_met,
    localized_activity_name,
)

# The app's Khác (custom activity) screen starts at 45 minutes.
UNLISTED_DEFAULT_MINUTES = 45
MINUTES_RANGE = (1.0, 600.0)
UNLISTED_MET_RANGE = (1.5, 18.0)
_INTENSITIES = {intensity.value for intensity in MovementIntensity}


@dataclass(frozen=True)
class DescribedActivity:
    activity_id: str | None  # None when the activity is not in the catalog
    name: str  # catalog English name (what the app's byTitle resolves), else the person's words
    duration_min: int
    intensity: str
    met: float


@dataclass(frozen=True)
class DescribedActivities:
    activities: list[DescribedActivity]
    dropped: int
    overflow: int


def normalize_activities(items: list[dict[str, Any]]) -> DescribedActivities:
    """Valid activities in sentence order, at most eight."""
    valid = [activity for activity in map(_activity, items) if activity is not None]
    return DescribedActivities(
        activities=valid[:DESCRIBE_MAX_ITEMS],
        dropped=len(items) - len(valid),
        overflow=max(0, len(valid) - DESCRIBE_MAX_ITEMS),
    )


def _activity(item: dict[str, Any]) -> DescribedActivity | None:
    activity_id = str(item.get("catalog_id") or "").strip()
    listed = get_activity(activity_id) is not None
    intensity = str(item.get("intensity") or "").strip().lower()
    if intensity not in _INTENSITIES:
        intensity = MovementIntensity.MODERATE.value

    minutes = finite(item.get("duration_min"))
    if minutes is None or minutes <= 0:
        minutes = (
            default_duration_min(activity_id) if listed else None
        ) or UNLISTED_DEFAULT_MINUTES
    duration_min = int(clamp(minutes, *MINUTES_RANGE) + 0.5)

    if listed:
        met = get_met(activity_id, intensity)
        name = localized_activity_name(activity_id, "en")
        if met is None or not name:
            return None
        return DescribedActivity(activity_id, name, duration_min, intensity, met)

    name = short_name(item.get("name"))
    met = finite(item.get("met"))
    if not name or met is None:
        return None
    return DescribedActivity(
        None, name, duration_min, intensity, clamp(met, *UNLISTED_MET_RANGE)
    )

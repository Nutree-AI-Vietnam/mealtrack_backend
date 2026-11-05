"""Turn the model's activity list into entries priced like the app's detail screen.

Listed activities take the catalog MET for their intensity and the app's
preset minutes; unlisted ones take the model's MET after it is clamped here,
or a default for their effort when the model gave none. An activity the
person named is never dropped for being outside the catalog; only an item
with no name is.
"""

import re
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
    activity_id_for_name,
    default_duration_min,
    get_activity,
    get_met,
    localized_activity_name,
)

# The app's Khác (custom activity) screen starts at 45 minutes.
UNLISTED_DEFAULT_MINUTES = 45
MINUTES_RANGE = (1.0, 600.0)
UNLISTED_MET_RANGE = (1.5, 18.0)
# Used when neither read of the sentence gave an unlisted activity a MET:
# generic light, moderate and vigorous effort from the Compendium.
UNLISTED_DEFAULT_MET = {
    MovementIntensity.LIGHT.value: 3.0,
    MovementIntensity.MODERATE.value: 4.5,
    MovementIntensity.HARD.value: 7.0,
}
_INTENSITIES = {intensity.value for intensity in MovementIntensity}
_ID_SEPARATORS = re.compile(r"[\s\-]+")


@dataclass(frozen=True)
class DescribedActivity:
    activity_id: str | None  # None when the activity is not in the catalog
    name: str  # catalog English name (what the app's byTitle resolves), else the person's words
    duration_min: int
    intensity: str
    met: float
    met_defaulted: bool = False  # unlisted, and no MET came from the model


@dataclass(frozen=True)
class DescribedActivities:
    activities: list[DescribedActivity]
    dropped: int
    overflow: int

    @property
    def defaulted(self) -> int:
        return sum(1 for activity in self.activities if activity.met_defaulted)


def normalize_activities(items: list[dict[str, Any]]) -> DescribedActivities:
    """Valid activities in sentence order, at most eight."""
    valid = [activity for activity in map(_activity, items) if activity is not None]
    return DescribedActivities(
        activities=valid[:DESCRIBE_MAX_ITEMS],
        dropped=len(items) - len(valid),
        overflow=max(0, len(valid) - DESCRIBE_MAX_ITEMS),
    )


def catalog_activity_id(item: dict[str, Any]) -> str | None:
    """The catalog id the item names, forgiving case and spacing in the id
    ("Gym Strength") and a catalog name in any app language ("Chạy bộ")."""
    raw = str(item.get("catalog_id") or "").strip()
    candidate = _ID_SEPARATORS.sub("_", raw.lower())
    if get_activity(candidate) is not None:
        return candidate
    for label in (raw, item.get("name")):
        found = activity_id_for_name(str(label or ""))
        if found is not None:
            return found
    return None


def needs_met(item: dict[str, Any]) -> bool:
    """An unlisted activity the person named but the model gave no MET."""
    return (
        catalog_activity_id(item) is None
        and bool(short_name(item.get("name")))
        and finite(item.get("met")) is None
    )


def _activity(item: dict[str, Any]) -> DescribedActivity | None:
    activity_id = catalog_activity_id(item)
    intensity = str(item.get("intensity") or "").strip().lower()
    if intensity not in _INTENSITIES:
        intensity = MovementIntensity.MODERATE.value

    minutes = finite(item.get("duration_min"))
    if minutes is None or minutes <= 0:
        minutes = default_duration_min(activity_id) or UNLISTED_DEFAULT_MINUTES
    duration_min = int(clamp(minutes, *MINUTES_RANGE) + 0.5)

    if activity_id is not None:
        met = get_met(activity_id, intensity)
        name = localized_activity_name(activity_id, "en")
        if met is None or not name:
            return None
        return DescribedActivity(activity_id, name, duration_min, intensity, met)

    name = short_name(item.get("name"))
    if not name:
        return None
    met = finite(item.get("met"))
    if met is None:
        return DescribedActivity(
            None,
            name,
            duration_min,
            intensity,
            UNLISTED_DEFAULT_MET[intensity],
            met_defaulted=True,
        )
    return DescribedActivity(
        None, name, duration_min, intensity, clamp(met, *UNLISTED_MET_RANGE)
    )

"""The Claude Design engine's sample weeks, for the weekly recap tests.

``weekly_recap_golden_vi_v1.json`` holds one sample week per story with the
design engine's Vietnamese output for it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.domain.model.weekly_recap import WeekStats, week_stats_from_dict

GOLDEN_PATH = Path(__file__).with_name("weekly_recap_golden_vi_v1.json")
GOLDEN: dict[str, Any] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
SAMPLES: dict[str, dict[str, Any]] = {case["name"]: case for case in GOLDEN["cases"]}


def sample_stats(name: str) -> WeekStats:
    """The sample week for one story, e.g. ``"steady"`` or ``"bulk"``.

    The design samples predate meal types; each of their meals is one type.
    """
    stats = week_stats_from_dict(SAMPLES[name]["stats"])
    return replace(
        stats, meal_types_by_day=stats.meal_types_by_day or stats.meals_by_day
    )

"""Weekly recap thresholds and display limits.

Defaults mirror the design engine. ``WEEKLY_RECAP_CONFIG_JSON`` can override
any field by name, plus per-insight ``priorities`` and per-case ``cases``
limits (``{"steady": {"grow": 2}}``). Values of the wrong type, negative
numbers and unknown keys are ignored and the default is kept.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

logger = logging.getLogger(__name__)

_MAPPINGS = frozenset({"priorities", "cases"})


@dataclass(frozen=True)
class RecapConfig:
    """Every tunable number the recap engine and stats builder use."""

    # Which story the week gets.
    low_data_meals: int = 10
    first_week_account_days: int = 14
    comeback_gap_days: int = 7
    comeback_min_days: int = 3
    tough_on_budget_max: int = 3
    tough_bulk_under_min: int = 4
    min_week_days: int = 3
    # Calorie bands, as a share of the day's target.
    on_budget_tolerance: float = 1.05
    under_ratio: float = 0.9
    # A day looks fully logged with two meal types and this share of its target.
    full_day_ratio: float = 0.7
    # Wins.
    protein_hit_days: int = 5
    protein_over_ratio: float = 1.03
    on_budget_days: int = 5
    training_fuel_ratio: float = 0.97
    moving_workouts: int = 3
    water_hit_days: int = 5
    fiber_hit_ratio: float = 0.9
    new_streak_gap: int = 7
    new_streak_min: int = 3
    learn_dishes_min: int = 2
    learn_protein_ratio: float = 0.8
    # Things to improve.
    fiber_low_ratio: float = 0.7
    lunch_no_veg_days: int = 4
    water_low_ratio: float = 0.85
    late_snack_days: int = 3
    late_snack_hour: int = 22
    sugary_drinks: int = 4
    weekend_delta_kcal: int = 400
    protein_low_days_max: int = 3
    protein_low_min_meals: int = 10
    rest_under_days: int = 2
    preworkout_carbs_ratio: float = 0.75
    # Habits and challenges.
    missed_meal_days: int = 4
    one_meal_missed_days: int = 3
    photo_share_max: float = 0.5
    streak_goal: int = 7
    fiber_challenge_ratio: float = 1.2
    fiber_challenge_step: int = 5
    # Display.
    max_cards: int = 3
    spark_protein_headroom: float = 1.15
    spark_kcal_headroom: float = 1.1
    spark_target_headroom: float = 1.05
    streak_progress_days: int = 30
    progress_weeks: int = 12
    fiber_impact_cap_g: int = 8
    water_impact_cap_l: float = 0.5
    protein_impact_g: int = 15
    rest_impact_step_kcal: int = 50
    # Stats builder.
    sugary_min_sugar_g: float = 10.0
    lunch_fiber_veg_g: float = 3.0
    pre_workout_window_min: int = 90
    # Overrides.
    priorities: Mapping[str, int] = field(default_factory=dict)
    cases: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    def pri(self, insight_id: str, default: int) -> int:
        """Sort priority for an insight, issue or habit."""
        return self.priorities.get(insight_id, default)

    def case_limit(self, case_id: str, key: str, default: int) -> int:
        """Per-case card or advice count (``grow`` / ``advice``)."""
        return self.cases.get(case_id, {}).get(key, default)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> RecapConfig:
        """Build a config from decoded JSON, keeping defaults for bad values."""
        values: dict[str, Any] = {}
        ignored = [key for key in raw if key not in {f.name for f in fields(cls)}]
        for item in fields(cls):
            if item.name not in raw:
                continue
            if item.name in _MAPPINGS:
                values[item.name] = _mapping(item.name, raw[item.name])
                continue
            value = _number(item.default, raw[item.name])
            if value is None:
                ignored.append(item.name)
            else:
                values[item.name] = value
        if ignored:
            logger.warning("Ignoring weekly recap config keys: %s", sorted(ignored))
        return cls(**values)

    @classmethod
    def from_json(cls, raw: str | None) -> RecapConfig:
        """Parse ``WEEKLY_RECAP_CONFIG_JSON``; empty or invalid means defaults."""
        if not raw or not raw.strip():
            return cls()
        try:
            decoded = json.loads(raw)
        except ValueError:
            logger.warning("WEEKLY_RECAP_CONFIG_JSON is not valid JSON; using defaults")
            return cls()
        if not isinstance(decoded, Mapping):
            logger.warning("WEEKLY_RECAP_CONFIG_JSON must be an object; using defaults")
            return cls()
        return cls.from_mapping(decoded)


def _number(default: Any, value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    if isinstance(default, int):
        return int(value) if float(value).is_integer() else None
    return float(value)


def _mapping(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    if name == "priorities":
        return _int_map(value)
    return {
        key: _int_map(limits)
        for key, limits in value.items()
        if isinstance(key, str) and isinstance(limits, Mapping)
    }


def _int_map(value: Mapping[Any, Any]) -> dict[str, int]:
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str)
        and isinstance(item, int)
        and not isinstance(item, bool)
        and item >= 0
    }

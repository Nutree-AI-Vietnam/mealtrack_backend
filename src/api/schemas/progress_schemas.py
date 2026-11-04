"""
Pydantic response schemas for progress screen endpoints.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def _trends_only() -> Any:
    """A field that is left out of the JSON when unset, so old payloads keep their shape."""
    return Field(None, exclude_if=lambda value: value is None)


class StreakRestoreState(BaseModel):
    """Restores the user holds and the missed days they can still restore."""

    balance: int  # paid restores; the app shows balance + 1 while free_available
    free_available: bool
    missed_dates: list[str] = []  # YYYY-MM-DD; empty means nothing to restore
    restorable_until: str | None = None  # YYYY-MM-DD, last day of the offer
    broken_streak: int = 0  # logged days of the run the missed days broke
    restored_dates: list[str] = []  # restored days of the last 14, YYYY-MM-DD


class StreakResponse(BaseModel):
    """Response for GET /v1/meals/streak."""

    current_streak: int
    best_streak: int
    last_logged_date: str | None = None  # YYYY-MM-DD, None if never logged
    scan_count: int = 0  # total meals created via scanner
    restore: StreakRestoreState | None = None


class DailyBreakdownEntry(BaseModel):
    """Macros consumed vs target for a single day."""

    date: str  # YYYY-MM-DD
    calories_consumed: float
    calories_target: float
    protein_consumed: float
    protein_target: float
    carbs_consumed: float
    carbs_target: float
    fat_consumed: float
    fat_target: float
    meal_count: int


class DailyBreakdownResponse(BaseModel):
    """Response for GET /v1/meals/weekly/daily-breakdown."""

    days: list[DailyBreakdownEntry]
    week_start: str  # YYYY-MM-DD (Monday)


class JourneyProgressAction(BaseModel):
    """Latest counted action inside the active journey period."""

    source: str
    label: str
    logged_at: str


class JourneyProgressBreakdown(BaseModel):
    """Current-day action score contribution."""

    calories_points: float = 0.0
    logging_points: float = 0.0
    protein_points: float = 0.0
    hydration_points: float = 0.0
    activity_points: float = 0.0


class JourneyProgressResponse(BaseModel):
    """Response for GET /v1/progress/journey."""

    period_start: str
    period_end: str
    as_of: str
    progress_percent: float
    current_period_progress_percent: float = 0.0
    journey_progress_seed_percent: float = 0.0
    confirmed_progress_percent: float
    provisional_progress_percent: float
    timeline_days: int
    daily_progress_budget_percent: float
    score: int = 0
    breakdown: JourneyProgressBreakdown
    latest_action: JourneyProgressAction | None = None
    is_week_over_budget: bool = False


class MealQualityCounts(BaseModel):
    """Rated meals of the day by score band: good >= 70, fair 50-69, low < 50."""

    good: int = 0
    fair: int = 0
    low: int = 0


class ProgressSummaryDay(BaseModel):
    """One calendar day in GET /v1/progress/summary."""

    date: str  # YYYY-MM-DD
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float = 0.0
    fiber_target_g: float = 0.0
    calories: float
    target_calories: float
    target_source: str  # adjusted_live | snapshot | base
    burned_calories: float
    hydration_ml: int
    hydration_goal_ml: int
    protein_target_g: float
    meal_count: int
    # meal_count without drinks; None on a row cached before the field existed.
    food_meal_count: int | None = None
    logged_status: str  # full | partial | none
    is_cheat_day: bool
    nrf_quality: float = 0.0
    nrf_coverage: int = 0
    iron_mg: float | None = None
    potassium_mg: float | None = None
    sodium_mg: float | None = None
    added_sugar_g: float | None = None
    # Sent only with include=trends; absent, not null, otherwise.
    micros: dict[str, float] | None = _trends_only()
    micro_meal_counts: dict[str, int] | None = _trends_only()
    meal_quality_counts: MealQualityCounts | None = _trends_only()
    quality_by_meal_type: dict[str, float] | None = _trends_only()
    movement_minutes: int | None = _trends_only()
    movement_sessions: int | None = _trends_only()
    movement_load: float | None = _trends_only()
    movement_kinds: list[str] | None = _trends_only()
    # The doses' share of ``micros`` (estimates included), the part from Ghi
    # nhanh estimates, preformed vitamin A (µg) from non-estimated doses, and
    # the dose count. Supplement folate is µg DFE (folic acid x 1.7).
    supplement_micros: dict[str, float] | None = _trends_only()
    supplement_estimated_micros: dict[str, float] | None = _trends_only()
    supplement_vitamin_a_preformed: float | None = _trends_only()
    supplement_doses: int | None = _trends_only()


class MicroUpperLimit(BaseModel):
    """Tolerable upper intake; scope says which intake it covers."""

    value: float
    scope: str  # all_sources | supplements_only | preformed_only


class ProgressSummaryResponse(BaseModel):
    """Server-defined window plus one row per served day."""

    effective_start: str
    effective_end: str
    cap_days: int
    days: list[ProgressSummaryDay]
    goals_revision: int | None = _trends_only()
    micro_goals: dict[str, float] | None = _trends_only()
    micro_upper_limits: dict[str, MicroUpperLimit] | None = _trends_only()


class ProgressRecapHighlight(BaseModel):
    """One numbered recap beat for the selected timeline."""

    kind: str
    polarity: str
    title: str
    detail: str


class ProgressRecapResponse(BaseModel):
    """AI recap for one Progress timeline window."""

    status: str
    horizon: str
    effective_start: str
    effective_end: str
    headline: str = ""
    body: str = ""
    next_move: str = ""
    highlights: list[ProgressRecapHighlight] = []
    generated_at: str | None = None

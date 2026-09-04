"""
Pydantic response schemas for progress screen endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel


class StreakResponse(BaseModel):
    """Response for GET /v1/meals/streak."""

    current_streak: int
    best_streak: int
    last_logged_date: str | None = None  # YYYY-MM-DD, None if never logged
    scan_count: int = 0  # total meals created via scanner


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
    logged_status: str  # full | partial | none
    is_cheat_day: bool
    nrf_quality: float = 0.0
    nrf_coverage: int = 0


class ProgressSummaryResponse(BaseModel):
    """Server-defined window plus one row per served day."""

    effective_start: str
    effective_end: str
    cap_days: int
    days: list[ProgressSummaryDay]

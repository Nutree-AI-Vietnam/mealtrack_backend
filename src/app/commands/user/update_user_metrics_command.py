"""
Command to update user metrics (weight, job type, training, body fat).
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class UpdateUserMetricsCommand:
    """Update user metrics (including fitness goal and target weight) and trigger TDEE recalculation."""

    user_id: str
    age: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    biological_sex: str | None = None
    job_type: str | None = None
    training_days_per_week: int | None = None
    training_minutes_per_session: int | None = None
    body_fat_percent: float | None = None
    body_fat_percent_provided: bool = False
    fitness_goal: str | None = None
    training_level: str | None = None
    target_weight_kg: float | None = None
    goal_start_weight_kg: float | None = None
    goal_started_at: datetime | None = None
    daily_water_goal_ml: int | None = None
    reset_water_goal: bool = False
    weight_loss_kg_per_week: float | None = None
    dietary_preferences: list[str] | None = None
    allergies: list[str] | None = None
    health_conditions: list[str] | None = None

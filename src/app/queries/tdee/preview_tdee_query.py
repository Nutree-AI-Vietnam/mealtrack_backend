"""
Query to preview TDEE calculation without authentication.
"""

from dataclasses import dataclass

from src.app.events.base import Query


@dataclass
class PreviewTdeeQuery(Query):
    """Query to preview TDEE calculation without persisting data."""

    age: int
    sex: str  # 'male' or 'female'
    height: float
    weight: float
    job_type: str
    training_days_per_week: int
    training_minutes_per_session: int
    goal: str
    body_fat_percentage: float | None = None
    unit_system: str = "metric"
    training_level: str | None = None
    diet_type: str = "classic"
    custom_protein_g: float | None = None
    custom_carbs_g: float | None = None
    custom_fat_g: float | None = None
    requested_calories: float | None = None
    weight_loss_kg_per_week: float | None = None

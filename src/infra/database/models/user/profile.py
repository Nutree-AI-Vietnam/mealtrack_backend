"""
User profile model for physical attributes and personal information.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin
from src.infra.database.models.user.profile_preference import UserProfilePreference


class UserProfile(Base, BaseMixin):
    """Stores user physical attributes and personal info with historical tracking."""

    __tablename__ = "user_profiles"

    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    age = Column(Integer, nullable=False)
    gender = Column(String(20), nullable=False)  # male, female, other
    height_cm = Column(Float, nullable=False)
    weight_kg = Column(Float, nullable=False)
    body_fat_percentage = Column(Float, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    is_current = Column(Boolean, default=True, nullable=False)

    # Goal fields (from UserGoal)
    job_type = Column(
        String(30), nullable=False, default="desk"
    )  # desk, on_feet, physical
    training_days_per_week = Column(Integer, nullable=False, default=0)  # 0-7
    training_minutes_per_session = Column(Integer, nullable=False, default=0)  # 15-180
    fitness_goal = Column(
        String(30), nullable=False, default="maintenance"
    )  # maintenance, cutting, bulking
    target_weight_kg = Column(Float, nullable=True)
    meals_per_day = Column(Integer, default=3, nullable=False)
    snacks_per_day = Column(Integer, default=1, nullable=False)

    # Training experience level (beginner, intermediate, advanced)
    training_level = Column(String(20), nullable=True, default=None)

    # Onboarding redesign fields (NM-44)
    challenge_duration = Column(String(30), nullable=True, default=None)
    training_types = Column(JSON, nullable=True, default=None)

    # Custom macro overrides — when ALL three non-null, overrides calculated macros
    custom_protein_g = Column(Float, nullable=True, default=None)
    custom_carbs_g = Column(Float, nullable=True, default=None)
    custom_fat_g = Column(Float, nullable=True, default=None)

    # Goal progress tracking — tracks when user started current goal journey
    goal_start_weight_kg = Column(Float, nullable=True, default=None)
    goal_started_at = Column(DateTime(timezone=True), nullable=True, default=None)
    journey_progress_seed_percent = Column(Float, nullable=False, default=0.0)
    daily_water_goal_ml = Column(Integer, nullable=True, default=None)
    # Monotonic fence for cached and weekly target materializations.
    profile_target_revision = Column(Integer, nullable=False, default=1)
    # Chosen lose-weight pace in kg per week. Null keeps the flat cutting deficit.
    weight_loss_kg_per_week = Column(Float, nullable=True, default=None)
    pace_clinician_disclaimer = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    weight_loss_pace_reset_on = Column(Date, nullable=True, default=None)

    @property
    def has_custom_macros(self) -> bool:
        """True when user has set custom macro overrides."""
        return (
            self.custom_protein_g is not None
            and self.custom_carbs_g is not None
            and self.custom_fat_g is not None
        )

    # Preference fields (from UserPreferences)
    dietary_preferences = Column(
        JSON, default=[], nullable=False
    )  # ['vegan', 'vegetarian', 'gluten_free', etc.]
    health_conditions = Column(
        JSON, default=[], nullable=False
    )  # ['diabetes', 'hypertension', etc.]
    allergies = Column(
        JSON, default=[], nullable=False
    )  # ['nuts', 'dairy', 'shellfish', etc.]
    pain_points = Column(JSON, default=[], nullable=True)

    # Attribution — JSON array, nullable for existing users
    referral_sources = Column(JSON, nullable=True, default=None)

    # Constraints
    __table_args__ = (
        CheckConstraint("age >= 12 AND age <= 120", name="check_age_range"),
        CheckConstraint("height_cm > 0", name="check_height_positive"),
        CheckConstraint("weight_kg > 0", name="check_weight_positive"),
        CheckConstraint(
            "body_fat_percentage IS NULL OR (body_fat_percentage >= 0 AND body_fat_percentage <= 100)",
            name="check_body_fat_range",
        ),
        Index("idx_user_current", "user_id", "is_current"),
        CheckConstraint(
            "daily_water_goal_ml IS NULL OR daily_water_goal_ml > 0",
            name="check_water_goal_positive",
        ),
        CheckConstraint(
            "journey_progress_seed_percent >= 0 AND journey_progress_seed_percent <= 100",
            name="check_journey_progress_seed_percent_range",
        ),
        CheckConstraint(
            "weight_loss_kg_per_week IS NULL "
            "OR (weight_loss_kg_per_week > 0 AND weight_loss_kg_per_week <= 1)",
            name="check_weight_loss_kg_per_week",
        ),
    )

    # Relationships
    user = relationship("User", back_populates="profiles")
    preference_entries = relationship(
        "UserProfilePreference",
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by=lambda: (
            UserProfilePreference.preference_type,
            UserProfilePreference.position,
            UserProfilePreference.id,
        ),
    )

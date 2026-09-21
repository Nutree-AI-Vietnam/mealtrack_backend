"""Weekly meal plan aggregate persistence model."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin


class WeeklyMealPlanORM(Base, TimestampMixin):
    """One owner-scoped Monday-to-Sunday plan."""

    __tablename__ = "weekly_meal_plans"

    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    week_start_date = Column(Date, nullable=False)
    status = Column(String(16), nullable=False, default="draft", server_default="draft")
    people = Column(Integer, nullable=False, default=1, server_default="1")
    preferences = Column(JSON, nullable=False, default=dict, server_default="{}")
    timezone = Column(String(64), nullable=False, server_default="UTC")
    daily_calories = Column(Integer, nullable=True)
    catalog_revision = Column(String(160), nullable=True)
    algorithm_version = Column(String(32), nullable=False, server_default="v1")
    revision = Column(Integer, nullable=False, default=1, server_default="1")

    slots = relationship(
        "WeeklyMealPlanSlotORM",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="(WeeklyMealPlanSlotORM.day_index, WeeklyMealPlanSlotORM.slot_index)",
        lazy="selectin",
    )
    pantry_items = relationship(
        "WeeklyMealPlanPantryItemORM",
        back_populates="plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index(
            "uq_weekly_meal_plans_user_week", "user_id", "week_start_date", unique=True
        ),
        Index("idx_weekly_meal_plans_user_status", "user_id", "status"),
        CheckConstraint(
            "status IN ('draft', 'confirmed')", name="ck_weekly_meal_plan_status"
        ),
        CheckConstraint("people BETWEEN 1 AND 6", name="ck_weekly_meal_plan_people"),
        CheckConstraint("revision > 0", name="ck_weekly_meal_plan_revision"),
    )

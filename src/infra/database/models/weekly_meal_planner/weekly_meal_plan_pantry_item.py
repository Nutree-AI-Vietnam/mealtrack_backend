"""Weekly meal plan pantry inventory."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin


class WeeklyMealPlanPantryItemORM(Base, TimestampMixin):
    """Quantity on hand for one canonical ingredient. Not a grocery checkbox."""

    __tablename__ = "weekly_meal_plan_pantry_items"

    id = Column(String(36), primary_key=True)
    plan_id = Column(
        String(36),
        ForeignKey("weekly_meal_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    food_reference_id = Column(
        Integer, ForeignKey("food_reference.id", ondelete="RESTRICT"), nullable=False
    )
    available_amount = Column(Numeric(12, 4), nullable=True)
    available_unit = Column(String(80), nullable=True)

    plan = relationship("WeeklyMealPlanORM", back_populates="pantry_items")

    __table_args__ = (
        Index(
            "uq_weekly_pantry_plan_food", "plan_id", "food_reference_id", unique=True
        ),
        CheckConstraint(
            "available_amount IS NULL OR available_amount >= 0",
            name="ck_weekly_pantry_available_amount",
        ),
    )

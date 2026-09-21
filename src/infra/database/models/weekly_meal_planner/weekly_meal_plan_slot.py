"""Weekly meal plan slot persistence model."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin


class WeeklyMealPlanSlotORM(Base, TimestampMixin):
    """One stable lunch or dinner coordinate in a weekly plan."""

    __tablename__ = "weekly_meal_plan_slots"

    id = Column(String(36), primary_key=True)
    plan_id = Column(
        String(36),
        ForeignKey("weekly_meal_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_index = Column(Integer, nullable=False)
    slot_index = Column(Integer, nullable=False)
    catalog_meal_id = Column(
        String(36), ForeignKey("meal_catalog.id", ondelete="SET NULL"), nullable=True
    )
    is_logged = Column(Boolean, nullable=False, default=False, server_default="false")
    logged_meal_id = Column(
        String(36), ForeignKey("meal.meal_id", ondelete="SET NULL"), nullable=True
    )
    version = Column(Integer, nullable=False, default=1, server_default="1")

    plan = relationship("WeeklyMealPlanORM", back_populates="slots")
    catalog_meal = relationship("MealCatalogORM", lazy="joined")

    __table_args__ = (
        Index(
            "uq_weekly_meal_plan_slots_coordinate",
            "plan_id",
            "day_index",
            "slot_index",
            unique=True,
        ),
        Index("idx_weekly_meal_plan_slots_catalog", "catalog_meal_id"),
        CheckConstraint("day_index BETWEEN 0 AND 6", name="ck_weekly_slot_day_index"),
        CheckConstraint("slot_index BETWEEN 0 AND 1", name="ck_weekly_slot_slot_index"),
        CheckConstraint("version > 0", name="ck_weekly_slot_version"),
    )

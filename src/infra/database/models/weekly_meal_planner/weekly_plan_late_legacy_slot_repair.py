"""Migration-owned record for reversing a late weekly slot repair."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String

from src.infra.database.base import Base


class WeeklyPlanLateLegacySlotRepairORM(Base):
    """Original revision and inserted breakfast ID for one repaired day."""

    __tablename__ = "weekly_plan_late_legacy_slot_repairs"

    plan_id = Column(
        String(36),
        ForeignKey("weekly_meal_plans.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    day_index = Column(Integer, primary_key=True, nullable=False)
    breakfast_slot_id = Column(String(36), nullable=False, unique=True)
    original_revision = Column(Integer, nullable=False)
    original_updated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (CheckConstraint("day_index BETWEEN 0 AND 6"),)

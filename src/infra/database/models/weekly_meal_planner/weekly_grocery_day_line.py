"""One day's grocery note on a weekly plan. Pantry stock stays separate."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin


class WeeklyGroceryDayLineORM(Base, TimestampMixin):
    """Needed amount and checked-off flag for one ingredient on one plan day."""

    __tablename__ = "weekly_grocery_day_lines"

    id = Column(String(36), primary_key=True)
    plan_id = Column(
        String(36),
        ForeignKey("weekly_meal_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    food_reference_id = Column(Integer, nullable=False)
    day_index = Column(Integer, nullable=False)
    needed_amount = Column(Numeric(12, 4), nullable=True)
    covered = Column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        Index(
            "uq_weekly_grocery_day_line",
            "plan_id",
            "food_reference_id",
            "day_index",
            unique=True,
        ),
        CheckConstraint(
            "day_index BETWEEN 0 AND 6",
            name="ck_weekly_grocery_day_line_day",
        ),
        CheckConstraint(
            "needed_amount IS NULL OR needed_amount >= 0",
            name="ck_weekly_grocery_day_line_amount",
        ),
        CheckConstraint(
            "covered OR needed_amount IS NOT NULL",
            name="ck_weekly_grocery_day_line_present",
        ),
    )

"""Grocery checkbox state. Quantities stay derived and are not stored here."""

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin


class WeeklyGroceryItemStateORM(Base, TimestampMixin):
    __tablename__ = "weekly_grocery_item_state"

    id = Column(String(36), primary_key=True)
    plan_id = Column(
        String(36),
        ForeignKey("weekly_meal_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    food_reference_id = Column(
        Integer, ForeignKey("food_reference.id", ondelete="RESTRICT"), nullable=False
    )
    checked = Column(Boolean, nullable=False, default=False, server_default="false")
    do_not_buy = Column(Boolean, nullable=False, default=False, server_default="false")
    manually_owned = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (
        Index(
            "uq_weekly_grocery_state_plan_food",
            "plan_id",
            "food_reference_id",
            unique=True,
        ),
    )

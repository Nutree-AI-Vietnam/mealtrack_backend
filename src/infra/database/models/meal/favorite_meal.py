"""Database model for durable favorite meals membership."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import relationship

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base


class FavoriteMealORM(Base):
    """Join table recording user favorites for meals."""

    __tablename__ = "favorite_meals"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    meal_id = Column(
        String(36),
        ForeignKey("meal.meal_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    favorited_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    # Relationships
    meal = relationship("MealORM", foreign_keys=[meal_id], lazy="selectin")
    user = relationship("User", foreign_keys=[user_id], lazy="noload")

    __table_args__ = (Index("ix_favorite_meals_meal_id", "meal_id"),)

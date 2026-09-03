"""Hydration entry database model."""

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class HydrationEntryORM(Base, BaseMixin):
    __tablename__ = "hydration_entries"

    id = Column(String(64), primary_key=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    drink_id = Column(String(64), nullable=True, index=True)
    drink_name_snapshot = Column(String(255), nullable=False)
    emoji_snapshot = Column(String(16), nullable=True)
    volume_ml = Column(Integer, nullable=False)
    credited_ml = Column(Integer, nullable=False)
    protein_g = Column(Float, nullable=False, default=0.0)
    carbs_g = Column(Float, nullable=False, default=0.0)
    fat_g = Column(Float, nullable=False, default=0.0)
    fiber_g = Column(Float, nullable=False, default=0.0)
    sugar_g = Column(Float, nullable=False, default=0.0)
    micros = Column(JSON, nullable=True)
    logged_at = Column(DateTime(timezone=True), nullable=False)
    source = Column(String(32), nullable=False, default="hydration")
    legacy_meal_id = Column(
        String(36),
        ForeignKey("meal.meal_id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    image_url = Column(String(512), nullable=True)

    __table_args__ = (
        Index("idx_hydration_entries_user_logged_at", "user_id", "logged_at"),
    )

"""Immutable per-day adjusted calorie target snapshot."""

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, String

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base


class DailyTargetSnapshotORM(Base):
    __tablename__ = "daily_target_snapshots"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    date = Column(Date, primary_key=True)
    adjusted_target_calories = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

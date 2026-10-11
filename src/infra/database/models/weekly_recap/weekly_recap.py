"""One stored weekly recap per user per Monday-to-Sunday week."""

from sqlalchemy import JSON, Column, Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.base import Base


class WeeklyRecapORM(Base):
    __tablename__ = "weekly_recaps"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    week_start = Column(Date, primary_key=True)
    case_id = Column(String(32), nullable=False)
    locale = Column(String(16), nullable=False)
    stats = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    payload = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

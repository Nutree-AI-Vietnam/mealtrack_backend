"""Vacation database model."""

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, String

from src.infra.database.base import Base


class VacationORM(Base):
    __tablename__ = "vacations"

    id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    ended_on = Column(Date, nullable=True)
    frozen_calories = Column(Float, nullable=False)
    frozen_protein = Column(Float, nullable=False)
    frozen_carbs = Column(Float, nullable=False)
    frozen_fat = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

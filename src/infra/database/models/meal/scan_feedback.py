"""Immutable baseline plus the latest explicitly shared, reviewable correction."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.database.base import Base


class ScanFeedbackORM(Base):
    __tablename__ = "meal_scan_feedback"

    meal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("meal.meal_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    corrected: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    correction_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_shared", index=True
    )
    submission_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

"""Supplement doses, kept next to meals like water."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base
from src.infra.database.models.base import BaseMixin


class SupplementIntakeORM(Base, BaseMixin):
    """``id`` is the client's UUID; ``nutrients`` is the dose's absolute snapshot."""

    __tablename__ = "supplement_intakes"

    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # History outlives catalog edits; a product with doses is never deleted.
    product_id = Column(
        String(36),
        ForeignKey("supplement_products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    units = Column(Float, nullable=False)
    taken_at = Column(DateTime(timezone=True), nullable=False)
    local_date = Column(Date, nullable=False)
    logged_on_day = Column(Boolean, nullable=False)
    nutrients = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    estimated = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("units > 0", name="ck_supplement_intakes_units"),
        Index("idx_supplement_intakes_user_local_date", "user_id", "local_date"),
    )

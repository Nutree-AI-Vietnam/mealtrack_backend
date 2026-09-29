"""Revision-scoped nutrition estimates kept outside canonical recipe payloads."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base


def _json_document():
    return JSON().with_variant(JSONB(), "postgresql")


class MealCatalogMicronutrientEnrichmentORM(Base):
    """One shared estimate and claim state for a catalog recipe revision."""

    __tablename__ = "meal_catalog_micronutrient_enrichment"

    id = Column(String(36), primary_key=True)
    catalog_meal_id = Column(
        String(36),
        ForeignKey("meal_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_hash = Column(String(64), nullable=False)
    micros = Column(_json_document(), nullable=False, default=dict)
    sources = Column(_json_document(), nullable=False, default=dict)
    status = Column(String(16), nullable=False)
    claim_token = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    retry_after = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "catalog_meal_id",
            "content_hash",
            name="uq_meal_catalog_micronutrient_revision",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_meal_catalog_micronutrient_hash",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'failed')",
            name="ck_meal_catalog_micronutrient_status",
        ),
    )

"""SQL-authoritative preparation jobs and versioned presentation translations."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base


class CatalogPreparationJobORM(Base):
    __tablename__ = "catalog_preparation_jobs"

    id = Column(String(36), primary_key=True)
    task = Column(String(24), nullable=False)
    catalog_meal_id = Column(
        String(36), ForeignKey("meal_catalog.id", ondelete="CASCADE"), nullable=False
    )
    input_facet_version = Column(String(64), nullable=False)
    locale = Column(String(8), nullable=False, default="", server_default="")
    contract_version = Column(String(32), nullable=False)
    status = Column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=5, server_default="5")
    available_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claim_token = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "task",
            "catalog_meal_id",
            "input_facet_version",
            "locale",
            "contract_version",
            name="uq_catalog_preparation_input",
        ),
        CheckConstraint(
            "task IN ('projection', 'micronutrients', 'translation')",
            name="ck_catalog_preparation_task",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'retry_wait', 'failed', 'superseded')",
            name="ck_catalog_preparation_status",
        ),
        CheckConstraint(
            "length(input_facet_version) = 64 AND length(contract_version) > 0",
            name="ck_catalog_preparation_version",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts BETWEEN 1 AND 20",
            name="ck_catalog_preparation_attempts",
        ),
        CheckConstraint(
            "(task IN ('projection', 'micronutrients') AND locale = '') OR (task = 'translation' AND length(locale) > 0)",
            name="ck_catalog_preparation_locale",
        ),
        CheckConstraint(
            "status != 'running' OR (claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_catalog_preparation_running_lease",
        ),
    )


class CatalogRecipeTranslationORM(Base):
    __tablename__ = "catalog_recipe_translations"

    id = Column(String(36), primary_key=True)
    catalog_meal_id = Column(
        String(36), ForeignKey("meal_catalog.id", ondelete="CASCADE"), nullable=False
    )
    input_facet_version = Column(String(64), nullable=False)
    locale = Column(String(8), nullable=False)
    contract_version = Column(String(32), nullable=False)
    translations = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    __table_args__ = (
        UniqueConstraint(
            "catalog_meal_id",
            "input_facet_version",
            "locale",
            "contract_version",
            name="uq_catalog_translation_input",
        ),
        CheckConstraint(
            "length(input_facet_version) = 64 AND length(locale) > 0 AND length(contract_version) > 0",
            name="ck_catalog_translation_version",
        ),
    )

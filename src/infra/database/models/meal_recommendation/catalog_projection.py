"""Rebuildable catalog read models and a shared publication fence."""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from src.infra.database.base import Base


class CatalogPublicationVersionORM(Base):
    __tablename__ = "catalog_publication_version"

    id = Column(Integer, primary_key=True)
    selection = Column(BigInteger, nullable=False, default=0, server_default="0")
    ingredients = Column(BigInteger, nullable=False, default=0, server_default="0")
    translation = Column(BigInteger, nullable=False, default=0, server_default="0")
    enrichment = Column(BigInteger, nullable=False, default=0, server_default="0")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    __table_args__ = (CheckConstraint("id = 1", name="ck_catalog_version_singleton"),)


class MealCatalogProjectionORM(Base):
    __tablename__ = "meal_catalog_projection"

    catalog_meal_id = Column(
        String(36), ForeignKey("meal_catalog.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version = Column(Integer, nullable=False)
    selection_digest = Column(String(64), nullable=False)
    ingredient_digest = Column(String(64), nullable=False)
    translation_digest = Column(String(64), nullable=False)
    enrichment_digest = Column(String(64), nullable=False)
    query_dirty = Column(Boolean, nullable=False, default=False, server_default="false")
    nutrition_dirty = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    translation_dirty = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    enrichment_dirty = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    name_casefold = Column(Text, nullable=False)
    cuisine_casefold = Column(Text, nullable=False)
    search_text = Column(Text, nullable=False)
    browse_constraint_text = Column(Text, nullable=False)
    selection_constraint_text = Column(Text, nullable=False)
    browse_vegetarian = Column(Boolean, nullable=False)
    browse_no_pork = Column(Boolean, nullable=False)
    selection_contains_meat = Column(Boolean, nullable=False)
    selection_contains_pork = Column(Boolean, nullable=False)
    non_meal = Column(Boolean, nullable=False)
    total_minutes = Column(Integer, nullable=False)
    popularity_rank = Column(Integer, nullable=True)
    breakfast_eligible = Column(Boolean, nullable=False)
    lunch_eligible = Column(Boolean, nullable=False)
    dinner_eligible = Column(Boolean, nullable=False)
    snack_eligible = Column(Boolean, nullable=False)
    publication_status = Column(String(16), nullable=False)
    nutrition_status = Column(String(16), nullable=False)
    protein_g = Column(Numeric(), nullable=False)
    carbs_g = Column(Numeric(), nullable=False)
    fat_g = Column(Numeric(), nullable=False)
    fiber_g = Column(Numeric(), nullable=False)
    sugar_g = Column(Numeric(), nullable=False)
    calories = Column(Integer, nullable=False)
    summary_payload = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MealCatalogProjectionAllergenORM(Base):
    """Casefolded constraint codes retain canonical reference identities."""

    __tablename__ = "meal_catalog_projection_allergens"

    catalog_meal_id = Column(
        String(36),
        ForeignKey("meal_catalog_projection.catalog_meal_id", ondelete="CASCADE"),
        primary_key=True,
    )
    allergen_id = Column(
        String(36),
        ForeignKey("allergen_reference.id", ondelete="CASCADE"),
        primary_key=True,
    )
    code_normalized = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

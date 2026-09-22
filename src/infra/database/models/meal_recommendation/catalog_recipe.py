"""Database models for the curated meal catalog."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.infra.database.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _json_document():
    """JSON on SQLite tests, JSONB on PostgreSQL."""

    return JSON().with_variant(JSONB(), "postgresql")


class MealCatalogORM(Base):
    """One active/imported catalog meal with display metadata only."""

    __tablename__ = "meal_catalog"

    id = Column(String(36), primary_key=True, default=_uuid)
    catalog_key = Column(String(160), nullable=False, unique=True)
    content_hash = Column(String(64), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    cuisine = Column(String(80), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    source_name = Column(String(255), nullable=True)
    source_url = Column(Text, nullable=True)
    prep_time_minutes = Column(Integer, nullable=True)
    cook_time_minutes = Column(Integer, nullable=True)
    tag = Column(String(160), nullable=True)
    allergens = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    equipment = Column(Text, nullable=True)
    base_servings = Column(Integer, nullable=True)
    serving_source = Column(String(64), nullable=True)
    serving_confidence = Column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    recipe_payload = Column(_json_document(), nullable=False, default=dict)
    payload_schema_version = Column(
        Integer, nullable=False, default=1, server_default="1"
    )
    payload_digest = Column(String(64), nullable=False)
    publication_status = Column(
        String(16), nullable=False, default="published", server_default="published"
    )
    nutrition_status = Column(
        String(16), nullable=False, default="ready", server_default="ready"
    )
    popularity_rank = Column(Integer, nullable=True)
    breakfast_eligible = Column(Boolean, nullable=False, default=False)
    lunch_eligible = Column(Boolean, nullable=False, default=False)
    dinner_eligible = Column(Boolean, nullable=False, default=False)
    snack_eligible = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    ingredients = relationship(
        "MealCatalogIngredientORM",
        back_populates="catalog_meal",
        cascade="all, delete-orphan",
        order_by="MealCatalogIngredientORM.position",
        lazy="selectin",
    )
    allergen_links = relationship(
        "MealCatalogAllergenORM",
        back_populates="catalog_meal",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    steps = relationship(
        "MealCatalogStepORM",
        back_populates="catalog_meal",
        cascade="all, delete-orphan",
        order_by="MealCatalogStepORM.step_number",
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("length(catalog_key) > 0", name="ck_meal_catalog_key"),
        CheckConstraint("length(content_hash) = 64", name="ck_meal_catalog_hash"),
        CheckConstraint("length(name) > 0", name="ck_meal_catalog_name"),
        CheckConstraint("length(cuisine) > 0", name="ck_meal_catalog_cuisine"),
        CheckConstraint(
            "popularity_rank IS NULL OR popularity_rank >= 0",
            name="ck_meal_catalog_popularity_rank_non_negative",
        ),
        CheckConstraint(
            "prep_time_minutes IS NULL OR prep_time_minutes >= 0",
            name="ck_meal_catalog_prep_time_non_negative",
        ),
        CheckConstraint(
            "cook_time_minutes IS NULL OR cook_time_minutes >= 0",
            name="ck_meal_catalog_cook_time_non_negative",
        ),
        CheckConstraint(
            "base_servings IS NULL OR base_servings > 0",
            name="ck_meal_catalog_base_servings_positive",
        ),
        CheckConstraint(
            "serving_confidence IN ('verified', 'estimated', 'unknown')",
            name="ck_meal_catalog_serving_confidence",
        ),
        CheckConstraint(
            "publication_status IN ('draft', 'published')",
            name="ck_meal_catalog_publication_status",
        ),
        CheckConstraint(
            "nutrition_status IN ('not_ready', 'ready')",
            name="ck_meal_catalog_nutrition_status",
        ),
        CheckConstraint(
            "length(payload_digest) = 64",
            name="ck_meal_catalog_payload_digest",
        ),
        CheckConstraint(
            "breakfast_eligible OR lunch_eligible OR dinner_eligible OR snack_eligible",
            name="ck_meal_catalog_has_eligible_meal_type",
        ),
        Index("idx_meal_catalog_active_cuisine", "is_active", "cuisine"),
        Index(
            "idx_meal_catalog_active_popularity",
            "is_active",
            "popularity_rank",
            "name",
            "id",
        ),
    )


class MealCatalogIngredientORM(Base):
    """Ingredient reference for one catalog meal."""

    __tablename__ = "meal_catalog_ingredients"

    id = Column(String(36), primary_key=True, default=_uuid)
    catalog_meal_id = Column(
        String(36),
        ForeignKey("meal_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    position = Column(Integer, nullable=False)
    food_reference_id = Column(
        Integer,
        ForeignKey("food_reference.id", ondelete="RESTRICT"),
        nullable=False,
    )
    display_name = Column(String(255), nullable=False)
    quantity = Column(Numeric(12, 4), nullable=False)
    unit = Column(String(80), nullable=False)
    category = Column(
        String(32), nullable=False, default="pantry", server_default="pantry"
    )
    quantity_text = Column(String(255), nullable=True)
    raw_text = Column(Text, nullable=True)
    is_optional = Column(Boolean, nullable=False, default=False, server_default="false")
    notes = Column(Text, nullable=True)

    catalog_meal = relationship("MealCatalogORM", back_populates="ingredients")
    food_reference = relationship("FoodReferenceModel", lazy="selectin")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_meal_catalog_ingredients_quantity"),
        CheckConstraint("position > 0", name="ck_meal_catalog_ingredients_position"),
        Index("idx_meal_catalog_ingredients_food_ref", "food_reference_id"),
        Index(
            "uq_meal_catalog_ingredients_position",
            "catalog_meal_id",
            "position",
            unique=True,
        ),
        CheckConstraint(
            "category IN ('produce', 'protein', 'pantry')",
            name="ck_meal_catalog_ingredients_category",
        ),
    )


class MealCatalogStepORM(Base):
    """Immutable ordered cooking step for a catalog meal."""

    __tablename__ = "meal_catalog_steps"

    id = Column(String(36), primary_key=True, default=_uuid)
    catalog_meal_id = Column(
        String(36),
        ForeignKey("meal_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)

    catalog_meal = relationship("MealCatalogORM", back_populates="steps")

    __table_args__ = (
        CheckConstraint("step_number > 0", name="ck_meal_catalog_step_number"),
        CheckConstraint("length(title) > 0", name="ck_meal_catalog_step_title"),
        CheckConstraint(
            "length(description) > 0", name="ck_meal_catalog_step_description"
        ),
        Index(
            "uq_meal_catalog_steps_meal_number",
            "catalog_meal_id",
            "step_number",
            unique=True,
        ),
    )

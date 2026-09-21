"""Database models for the curated meal catalog."""

from __future__ import annotations

import uuid

from sqlalchemy import (
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
from sqlalchemy.orm import relationship

from src.infra.database.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


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
        order_by="MealCatalogIngredientORM.display_name",
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

    catalog_meal_id = Column(
        String(36),
        ForeignKey("meal_catalog.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    food_reference_id = Column(
        Integer,
        ForeignKey("food_reference.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    )
    display_name = Column(String(255), nullable=False)
    quantity = Column(Numeric(12, 4), nullable=False)
    unit = Column(String(80), nullable=False)
    category = Column(
        String(32), nullable=False, default="pantry", server_default="pantry"
    )

    catalog_meal = relationship("MealCatalogORM", back_populates="ingredients")
    food_reference = relationship("FoodReferenceModel", lazy="selectin")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_meal_catalog_ingredients_quantity"),
        Index("idx_meal_catalog_ingredients_food_ref", "food_reference_id"),
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

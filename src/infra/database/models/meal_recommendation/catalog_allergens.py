"""Normalized allergen references used as a hard planner constraint."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, ForeignKey, String
from sqlalchemy.orm import relationship

from src.infra.database.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AllergenReferenceORM(Base):
    __tablename__ = "allergen_reference"

    id = Column(String(36), primary_key=True, default=_uuid)
    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=False)


class MealCatalogAllergenORM(Base):
    __tablename__ = "meal_catalog_allergens"

    catalog_meal_id = Column(
        String(36),
        ForeignKey("meal_catalog.id", ondelete="CASCADE"),
        primary_key=True,
    )
    allergen_id = Column(
        String(36),
        ForeignKey("allergen_reference.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source = Column(String(32), nullable=False)
    confidence = Column(String(16), nullable=True)

    catalog_meal = relationship("MealCatalogORM", back_populates="allergen_links")
    allergen = relationship("AllergenReferenceORM", lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "source IN ('explicit', 'ingredient_derived', 'ai_derived')",
            name="ck_meal_catalog_allergen_source",
        ),
    )

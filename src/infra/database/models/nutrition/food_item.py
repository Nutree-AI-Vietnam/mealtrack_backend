"""
Food item model for individual food components within a meal.
"""

from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import PrimaryEntityMixin


class FoodItemORM(Base, PrimaryEntityMixin):
    """Database model for food items in a meal."""

    __tablename__ = "food_item"  # Explicit table name to match migration

    name = Column(String(255), nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String(120), nullable=False)
    confidence = Column(Float, nullable=True)
    nutrition_override = Column(JSON, nullable=True)
    source_kind = Column(String(32), nullable=True)
    source_food_id = Column(String(255), nullable=True)
    nutrition_contract_version = Column(String(64), nullable=True)
    source_snapshot = Column(JSON, nullable=True)

    # Edit support fields
    fdc_id = Column(
        Integer, nullable=True
    )  # USDA FDC ID (legacy, use food_reference_id)
    food_reference_id = Column(Integer, ForeignKey("food_reference.id"), nullable=True)
    is_custom = Column(
        Boolean, default=False, nullable=False
    )  # Whether this is a custom ingredient

    # Macro fields (previously in separate Macros table)
    protein = Column(Float, default=0, nullable=False)
    carbs = Column(Float, default=0, nullable=False)
    fat = Column(Float, default=0, nullable=False)
    fiber = Column(Float, default=0, nullable=False)
    sugar = Column(Float, default=0, nullable=False)

    # Foreign keys
    nutrition_id = Column(
        Integer, ForeignKey("nutrition.id"), nullable=True
    )  # Nullable for orphaned items

    # Soft delete
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="false")

    # Position ordering within meal
    order_index = Column(Integer, default=0, nullable=False, server_default="0")

    # Relationships
    nutrition = relationship("NutritionORM", back_populates="food_items")

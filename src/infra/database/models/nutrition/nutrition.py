"""
Nutrition model for overall nutritional information of a meal.
"""

from sqlalchemy import JSON, Column, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import SecondaryEntityMixin


class NutritionORM(Base, SecondaryEntityMixin):
    """Database model for nutrition data."""

    __tablename__ = "nutrition"

    confidence_score = Column(Float, nullable=True)
    raw_ai_response = Column(Text, nullable=True)
    nutrition_override = Column(JSON, nullable=True)

    # Macro fields -- calories are always derived: P*4 + (C-fiber)*4 + fiber*2 + F*9
    protein = Column(Float, default=0, nullable=False)
    carbs = Column(Float, default=0, nullable=False)
    fat = Column(Float, default=0, nullable=False)
    fiber = Column(Float, default=0, nullable=False)
    sugar = Column(Float, default=0, nullable=False)
    micros = Column(JSON, nullable=True)

    # Relationships
    food_items = relationship(
        "FoodItemORM",
        back_populates="nutrition",
        cascade="all, delete-orphan",
        order_by="FoodItemORM.order_index",
        lazy="raise",
    )
    meal_id = Column(String(36), ForeignKey("meal.meal_id"), nullable=False)
    meal = relationship("MealORM", back_populates="nutrition")

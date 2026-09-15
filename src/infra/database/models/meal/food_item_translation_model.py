"""
Food item translation database model.

See meal-translation-model.py for full implementation.
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from src.infra.database.base import Base


class FoodItemTranslationORM(Base):
    """
    Database model for food item translations.

    Stores translated name and description for each food item.
    """

    __tablename__ = "food_item_translation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meal_translation_id = Column(
        Integer,
        ForeignKey("meal_translation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    food_item_id = Column(
        String(36), nullable=False, index=True
    )  # Reference only, no FK
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Soft delete
    is_deleted = Column(Boolean, default=False, nullable=False, server_default="false")

    # Relationship back to meal translation
    meal_translation = relationship("MealTranslationORM", back_populates="food_items")

"""
Meal image model for storing image metadata.
"""

from sqlalchemy import Column, Integer, String, Text

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin


class MealImageORM(Base, TimestampMixin):
    """Database model for meal images."""

    __tablename__ = "mealimage"

    # Primary key
    image_id = Column(String(36), primary_key=True)
    format = Column(String(10), nullable=False)  # jpeg, png, etc.
    size_bytes = Column(Integer, nullable=False)
    width = Column(Integer, nullable=True)  # Changed to nullable
    height = Column(Integer, nullable=True)  # Changed to nullable
    url = Column(Text, nullable=True)  # Cloudinary / CDN image URL

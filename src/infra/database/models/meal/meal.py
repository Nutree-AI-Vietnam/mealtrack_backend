"""
Meal model for the main meal entity.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.models.base import TimestampMixin
from src.infra.database.models.enums import MealStatusEnum


class MealORM(Base, TimestampMixin):
    """Database model for meals."""

    __tablename__ = "meal"

    # Primary key
    meal_id = Column(String(36), primary_key=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )  # User who created this meal
    status = Column(Enum(MealStatusEnum, native_enum=False), nullable=False)
    dish_name = Column(String(255), nullable=True)  # The name of the dish
    meal_type = Column(String(20), nullable=True)  # breakfast, lunch, dinner, snack
    ready_at = Column(
        DateTime(timezone=True), nullable=True
    )  # When meal analysis was completed
    error_message = Column(Text, nullable=True)
    raw_ai_response = Column(Text, nullable=True)
    food_label_metadata = Column(JSON, nullable=True)

    # Edit tracking fields
    last_edited_at = Column(
        DateTime(timezone=True), nullable=True
    )  # When meal was last edited
    edit_count = Column(Integer, default=0, nullable=False)  # Number of times edited
    is_manually_edited = Column(
        Boolean, default=False, nullable=False
    )  # Whether meal has been manually edited

    # Source tracking (scanner, prompt, food_search, manual)
    source = Column(String(20), nullable=True)
    catalog_meal_id = Column(
        String(36),
        ForeignKey("meal_catalog.id", ondelete="SET NULL"),
        nullable=True,
    )
    catalog_meal_content_hash = Column(String(64), nullable=True)
    recipe_snapshot = Column(JSON().with_variant(JSONB(), "postgresql"), nullable=True)
    nutrition_snapshot = Column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )

    # Generic serving quantity: grams for food meals, ml for hydration meals
    quantity = Column(Integer, nullable=True)

    # Recipe details (populated for AI suggestions)
    description = Column(Text, nullable=True)
    instructions = Column(JSON, nullable=True)  # List[str]
    prep_time_min = Column(Integer, nullable=True)
    cook_time_min = Column(Integer, nullable=True)
    cuisine_type = Column(String(50), nullable=True)
    origin_country = Column(String(50), nullable=True)
    emoji = Column(String(8), nullable=True)  # AI-assigned food emoji

    # Relationships
    image_id = Column(String(36), ForeignKey("mealimage.image_id"), nullable=True)
    image = relationship("MealImageORM", uselist=False, lazy="joined")
    nutrition = relationship(
        "NutritionORM",
        uselist=False,
        back_populates="meal",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    translations = relationship(
        "MealTranslationORM",
        back_populates="meal",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    instruction_steps = relationship(
        "MealInstructionStepORM",
        back_populates="meal",
        cascade="all, delete-orphan",
        order_by="MealInstructionStepORM.position",
        lazy="selectin",
    )

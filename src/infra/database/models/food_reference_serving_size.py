"""Normalized serving-size rows for canonical foods."""

from sqlalchemy import Boolean, Column, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from src.infra.database.base import Base


class FoodReferenceServingSizeModel(Base):
    """Queryable serving conversion for a food reference."""

    __tablename__ = "food_reference_serving_sizes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    food_reference_id = Column(
        Integer,
        ForeignKey("food_reference.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(100), nullable=False)
    description = Column(String(100), nullable=True)
    name_vi = Column(String(100), nullable=True)
    grams = Column(Float, nullable=True)
    milliliters = Column(Float, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    position = Column(Integer, nullable=False, default=0)

    food_reference = relationship(
        "FoodReferenceModel",
        back_populates="serving_size_rows",
    )

    __table_args__ = (
        Index(
            "idx_food_reference_serving_sizes_ref_position",
            "food_reference_id",
            "position",
        ),
    )

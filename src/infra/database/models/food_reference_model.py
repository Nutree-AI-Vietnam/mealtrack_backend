"""
Food reference database model — canonical food catalog.
Evolved from barcode_products to serve as the single source of truth
for per-100g nutrition data across barcode scans, USDA, and fatsecret.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Computed,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from src.infra.database.base import Base
from src.infra.database.food_reference_search_name_sql import (
    FoodReferenceSearchName,
)


class FoodReferenceModel(Base):
    """Database model for canonical food reference entries."""

    __tablename__ = "food_reference"

    id = Column(Integer, primary_key=True, autoincrement=True)
    barcode = Column(String(20), unique=True, nullable=True, index=True)
    name = Column(String(255), nullable=False)
    name_normalized = Column(String(255), nullable=True, unique=True, index=True)
    name_vi = Column(String(255), nullable=True)
    # Accent-folded "name_vi name" words for search; maintained by the database.
    name_search = Column(Text, Computed(FoodReferenceSearchName(), persisted=True))
    brand = Column(String(255), nullable=True)
    category = Column(String(100), nullable=True, index=True)
    region = Column(String(10), nullable=False, default="global")
    fdc_id = Column(Integer, nullable=True, index=True)
    source_namespace = Column(String(64), nullable=True)
    source_food_id = Column(String(255), nullable=True)

    # Materialized, versioned eligibility. Editorial verification remains
    # separate so quarantine never silently rewrites reviewer intent.
    integrity_status = Column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    integrity_policy_version = Column(String(64), nullable=True)
    integrity_checked_at = Column(DateTime(timezone=True), nullable=True)
    integrity_reason = Column(String(128), nullable=True)
    integrity_input_digest = Column(String(64), nullable=True)
    integrity_review_reference = Column(String(255), nullable=True)

    # Nutrition per 100g — calories always derived from macros
    protein_100g = Column(Float, nullable=True)
    carbs_100g = Column(Float, nullable=True)
    fat_100g = Column(Float, nullable=True)
    fiber_100g = Column(Float, nullable=False, default=0)
    sugar_100g = Column(Float, nullable=False, default=0)

    # Extended nutrients (calcium_mg, iron_mg, vitamin_a_mcg, etc.)
    extra_nutrients = Column(JSON, nullable=True)

    # Conversion data
    serving_sizes = Column(JSON, nullable=True)  # [{name, grams}, ...]
    density = Column(Float, nullable=False, default=1.0)  # g/ml
    serving_size = Column(String(100), nullable=True)  # legacy text

    # Metadata
    source = Column(String(50), nullable=False, default="fatsecret")
    is_verified = Column(Boolean, nullable=False, default=False)
    image_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    serving_size_rows = relationship(
        "FoodReferenceServingSizeModel",
        back_populates="food_reference",
        cascade="all, delete-orphan",
        order_by="FoodReferenceServingSizeModel.position",
        lazy="selectin",
    )
    nutrient_rows = relationship(
        "FoodReferenceNutrientModel",
        back_populates="food_reference",
        cascade="all, delete-orphan",
        order_by="FoodReferenceNutrientModel.nutrient_key",
        lazy="selectin",
    )

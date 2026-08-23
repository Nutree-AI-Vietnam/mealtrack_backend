"""
Barcode product response DTOs.
"""

from typing import Optional

from pydantic import BaseModel, Field

class BarcodeProductResponse(BaseModel):
    """Response DTO for barcode product lookup."""

    name: str = Field(..., description="Product name")
    brand: Optional[str] = Field(None, description="Product brand")
    barcode: str = Field(..., description="Product barcode")
    protein_100g: Optional[float] = Field(None, description="Protein per 100g")
    carbs_100g: Optional[float] = Field(None, description="Carbohydrates per 100g")
    fat_100g: Optional[float] = Field(None, description="Fat per 100g")
    fiber_100g: float = Field(0, description="Fiber per 100g")
    sugar_100g: float = Field(0, description="Sugar per 100g")
    serving_size: Optional[str] = Field(None, description="Serving size description")
    image_url: Optional[str] = Field(None, description="Product image URL")
    source: Optional[str] = Field(
        None,
        description=(
            "Data source: cache, fatsecret, openfoodfacts, usda_fdc, "
            "brave_search, fatsecret_name_search, ai_estimate"
        ),
    )
    provider_source: Optional[str] = Field(
        None, description="Original provider source when source is cache"
    )
    food_reference_id: Optional[int] = Field(
        None, description="Food reference table ID"
    )
    origin: str | None = Field(None, description="Canonical nutrition origin")
    source_namespace: str | None = Field(
        None, description="Opaque provider source namespace"
    )
    source_food_id: str | None = Field(
        None, description="Opaque provider food identifier"
    )
    is_estimate: bool = Field(
        False, description="True when macros are AI-estimated, user should verify"
    )
    serving_options: list[dict] = Field(
        default_factory=list, description="Provider-specific serving choices"
    )

    model_config = {"from_attributes": True}

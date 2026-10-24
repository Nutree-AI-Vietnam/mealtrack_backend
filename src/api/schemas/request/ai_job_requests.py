"""Background AI job request DTOs."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.domain.model.ai_jobs import AI_JOB_MAX_SEEN_IDS
from src.domain.services.prompts.input_sanitizer import validate_refinement_items


class AiJobCreateRequest(BaseModel):
    """``input`` is validated against ``type`` by the route."""

    id: UUID = Field(..., description="Client-generated job id; re-sending is safe")
    type: str = Field(
        ..., min_length=1, max_length=32, description="meal_scan or parse_text"
    )
    input: dict[str, Any] = Field(default_factory=dict)


class MealScanJobInput(BaseModel):
    """Same fields as the scan-by-url endpoints."""

    image_url: str = Field(..., min_length=1, max_length=2048)
    image_id: str = Field(..., min_length=1, max_length=255)
    target_date: str | None = Field(default=None, max_length=10)
    user_description: str | None = Field(default=None, max_length=500)
    scan_mode: Literal["scanner", "food_label"] = "scanner"
    label_crop_image_url: str | None = Field(default=None, max_length=2048)
    label_crop_image_id: str | None = Field(default=None, max_length=255)
    crop_metadata: dict[str, Any] | None = None


class ParseTextJobInput(BaseModel):
    """Same fields as ``POST /v1/meals/parse-text``."""

    text: str = Field(..., min_length=1, max_length=500)
    current_items: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def validate_refinement_context(self) -> "ParseTextJobInput":
        self.current_items = validate_refinement_items(self.current_items)
        return self


class AiJobSeenRequest(BaseModel):
    ids: list[UUID] = Field(default_factory=list, max_length=AI_JOB_MAX_SEEN_IDS)

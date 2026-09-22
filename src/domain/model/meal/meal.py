import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from src.domain.utils.timezone_utils import format_iso_utc, utc_now

from ..nutrition import Nutrition
from .meal_image import MealImage
from .meal_translation_domain_models import MealTranslation


class MealStatus(Enum):
    """Status of a meal in the analysis pipeline."""

    PROCESSING = "PROCESSING"  # Initial state, waiting for analysis
    ANALYZING = "ANALYZING"  # AI analysis in progress
    ENRICHING = "ENRICHING"  # Enrichment with food database in progress
    READY = "READY"  # Final state, analysis complete
    FAILED = "FAILED"  # Analysis failed
    INACTIVE = "INACTIVE"  # Soft-deleted by user; ignored in UI/macros

    def __str__(self):
        return self.value


@dataclass
class Meal:
    """
    Aggregate root representing a meal with its image and nutritional information.
    This is the main entity in the domain.
    """

    meal_id: str  # UUID as string
    user_id: str  # UUID as string - identifies the user who owns this meal
    status: MealStatus
    created_at: datetime
    image: MealImage | None
    dish_name: str | None = None
    nutrition: Nutrition | None = None
    ready_at: datetime | None = None
    error_message: str | None = None
    raw_gpt_json: str | None = None
    food_label_metadata: dict | None = None
    # Edit tracking fields
    updated_at: datetime | None = None
    last_edited_at: datetime | None = None
    edit_count: int = 0
    is_manually_edited: bool = False
    meal_type: str | None = None
    translations: dict[str, MealTranslation] | None = None
    # Source tracking (scanner, prompt, food_search, manual)
    source: str | None = None
    catalog_meal_id: str | None = None
    catalog_meal_content_hash: str | None = None
    recipe_snapshot: dict | None = None
    nutrition_snapshot: dict | None = None
    # Recipe details (populated for AI suggestions)
    description: str | None = None
    instructions: list | None = (
        None  # List[str] (legacy) or List[dict] with {instruction, duration_minutes}
    )
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    cuisine_type: str | None = None
    origin_country: str | None = None
    emoji: str | None = None  # AI-assigned food emoji, stored once on creation
    # Generic serving quantity: grams for food, ml for hydration
    quantity: int | None = None

    def __post_init__(self):
        """Validate invariants."""
        # Validate UUID formats
        try:
            uuid.UUID(self.meal_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format for meal_id: {self.meal_id}") from e

        try:
            uuid.UUID(self.user_id)
        except ValueError as e:
            raise ValueError(f"Invalid UUID format for user_id: {self.user_id}") from e

        # Status-based validations
        if self.status == MealStatus.READY and self.nutrition is None:
            raise ValueError("Meal with READY status must have nutrition data")

        if self.status == MealStatus.READY and self.ready_at is None:
            raise ValueError("Meal with READY status must have ready_at timestamp")

        if self.status == MealStatus.FAILED and self.error_message is None:
            raise ValueError("Meal with FAILED status must have error_message")
        # INACTIVE has no additional constraints

    @classmethod
    def create_new_processing(cls, user_id: str, image: MealImage) -> "Meal":
        """Factory method to create a new meal in PROCESSING status."""
        return cls(
            meal_id=str(uuid.uuid4()),
            user_id=user_id,
            status=MealStatus.PROCESSING,
            created_at=utc_now(),
            image=image,
        )

    def _recipe_fields(self) -> dict:
        """Return recipe fields dict for state transitions."""
        return {
            "description": self.description,
            "instructions": self.instructions,
            "prep_time_min": self.prep_time_min,
            "cook_time_min": self.cook_time_min,
            "cuisine_type": self.cuisine_type,
            "origin_country": self.origin_country,
            "emoji": self.emoji,
            "catalog_meal_id": self.catalog_meal_id,
            "catalog_meal_content_hash": self.catalog_meal_content_hash,
            "recipe_snapshot": self.recipe_snapshot,
            "nutrition_snapshot": self.nutrition_snapshot,
        }

    def mark_analyzing(self) -> "Meal":
        """Transition to ANALYZING state."""
        return Meal(
            meal_id=self.meal_id,
            user_id=self.user_id,
            status=MealStatus.ANALYZING,
            created_at=self.created_at,
            image=self.image,
            dish_name=self.dish_name,
            nutrition=self.nutrition,
            ready_at=self.ready_at,
            error_message=self.error_message,
            raw_gpt_json=self.raw_gpt_json,
            food_label_metadata=self.food_label_metadata,
            updated_at=self.updated_at,
            last_edited_at=self.last_edited_at,
            edit_count=self.edit_count,
            is_manually_edited=self.is_manually_edited,
            meal_type=self.meal_type,
            translations=self.translations,
            source=self.source,
            **self._recipe_fields(),
        )

    def mark_enriching(self, raw_gpt_json: str) -> "Meal":
        """Transition to ENRICHING state with GPT response."""
        return Meal(
            meal_id=self.meal_id,
            user_id=self.user_id,
            status=MealStatus.ENRICHING,
            created_at=self.created_at,
            image=self.image,
            dish_name=self.dish_name,
            nutrition=self.nutrition,
            ready_at=self.ready_at,
            error_message=self.error_message,
            raw_gpt_json=raw_gpt_json,
            food_label_metadata=self.food_label_metadata,
            updated_at=self.updated_at,
            last_edited_at=self.last_edited_at,
            edit_count=self.edit_count,
            is_manually_edited=self.is_manually_edited,
            meal_type=self.meal_type,
            translations=self.translations,
            source=self.source,
            **self._recipe_fields(),
        )

    def mark_ready(
        self,
        nutrition: Nutrition,
        dish_name: str,
        raw_gpt_json: str | None = None,
        emoji: str | None = None,
    ) -> "Meal":
        """Transition to READY state with final nutrition data."""
        return Meal(
            meal_id=self.meal_id,
            user_id=self.user_id,
            status=MealStatus.READY,
            created_at=self.created_at,
            image=self.image,
            dish_name=dish_name,
            nutrition=nutrition,
            ready_at=utc_now(),
            error_message=self.error_message,
            raw_gpt_json=(
                raw_gpt_json if raw_gpt_json is not None else self.raw_gpt_json
            ),
            food_label_metadata=self.food_label_metadata,
            updated_at=self.updated_at,
            last_edited_at=self.last_edited_at,
            edit_count=self.edit_count,
            is_manually_edited=self.is_manually_edited,
            meal_type=self.meal_type,
            translations=self.translations,
            source=self.source,
            description=self.description,
            instructions=self.instructions,
            prep_time_min=self.prep_time_min,
            cook_time_min=self.cook_time_min,
            cuisine_type=self.cuisine_type,
            origin_country=self.origin_country,
            emoji=emoji if emoji is not None else self.emoji,
            catalog_meal_id=self.catalog_meal_id,
            catalog_meal_content_hash=self.catalog_meal_content_hash,
            recipe_snapshot=self.recipe_snapshot,
            nutrition_snapshot=self.nutrition_snapshot,
        )

    def mark_failed(self, error_message: str) -> "Meal":
        """Transition to FAILED state with error message."""
        return Meal(
            meal_id=self.meal_id,
            user_id=self.user_id,
            status=MealStatus.FAILED,
            created_at=self.created_at,
            image=self.image,
            dish_name=self.dish_name,
            nutrition=self.nutrition,
            ready_at=self.ready_at,
            error_message=error_message,
            raw_gpt_json=self.raw_gpt_json,
            food_label_metadata=self.food_label_metadata,
            updated_at=self.updated_at,
            last_edited_at=self.last_edited_at,
            edit_count=self.edit_count,
            is_manually_edited=self.is_manually_edited,
            meal_type=self.meal_type,
            translations=self.translations,
            source=self.source,
            **self._recipe_fields(),
        )

    def mark_edited(
        self,
        nutrition: Nutrition,
        dish_name: str,
        created_at: datetime | None = None,
        meal_type: str | None = None,
    ) -> "Meal":
        """Mark meal as edited with updated nutrition."""
        return Meal(
            meal_id=self.meal_id,
            user_id=self.user_id,
            status=MealStatus.READY,
            created_at=created_at or self.created_at,
            image=self.image,
            dish_name=dish_name,
            nutrition=nutrition,
            ready_at=self.ready_at,
            error_message=self.error_message,
            raw_gpt_json=self.raw_gpt_json,
            food_label_metadata=self.food_label_metadata,
            updated_at=utc_now(),
            last_edited_at=utc_now(),
            edit_count=self.edit_count + 1,
            is_manually_edited=True,
            meal_type=meal_type or self.meal_type,
            translations=self.translations,
            source=self.source,
            **self._recipe_fields(),
        )

    def with_image(self, image: MealImage | None) -> "Meal":
        """Return the meal with updated image metadata."""
        return Meal(
            meal_id=self.meal_id,
            user_id=self.user_id,
            status=self.status,
            created_at=self.created_at,
            image=image,
            dish_name=self.dish_name,
            nutrition=self.nutrition,
            ready_at=self.ready_at,
            error_message=self.error_message,
            raw_gpt_json=self.raw_gpt_json,
            food_label_metadata=self.food_label_metadata,
            updated_at=utc_now(),
            last_edited_at=self.last_edited_at,
            edit_count=self.edit_count,
            is_manually_edited=self.is_manually_edited,
            meal_type=self.meal_type,
            translations=self.translations,
            source=self.source,
            quantity=self.quantity,
            **self._recipe_fields(),
        )

    def without_image(self) -> "Meal":
        """Return the meal with no attached image."""
        return self.with_image(None)

    def mark_inactive(self) -> "Meal":
        """Mark meal as INACTIVE (soft delete)."""
        return Meal(
            meal_id=self.meal_id,
            user_id=self.user_id,
            status=MealStatus.INACTIVE,
            created_at=self.created_at,
            image=self.image,
            dish_name=self.dish_name,
            nutrition=self.nutrition,
            ready_at=self.ready_at,
            error_message=self.error_message,
            raw_gpt_json=self.raw_gpt_json,
            food_label_metadata=self.food_label_metadata,
            updated_at=utc_now(),
            last_edited_at=self.last_edited_at,
            edit_count=self.edit_count,
            is_manually_edited=self.is_manually_edited,
            meal_type=self.meal_type,
            translations=self.translations,
            source=self.source,
            quantity=self.quantity,
            **self._recipe_fields(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        result: dict[str, Any] = {
            "meal_id": self.meal_id,
            "user_id": self.user_id,
            "status": str(self.status),
            "created_at": format_iso_utc(self.created_at),
            "image": self.image.to_dict() if self.image else None,
        }

        if self.dish_name is not None:
            result["dish_name"] = self.dish_name

        if self.nutrition is not None:
            result["nutrition"] = self.nutrition.to_dict()

        if self.ready_at is not None:
            result["ready_at"] = format_iso_utc(self.ready_at)

        if self.error_message is not None:
            result["error_message"] = self.error_message

        if self.translations is not None:
            result["translations"] = {
                lang: trans.to_dict() for lang, trans in self.translations.items()
            }

        if self.description is not None:
            result["description"] = self.description
        if self.instructions is not None:
            result["instructions"] = self.instructions
        if self.prep_time_min is not None:
            result["prep_time_min"] = self.prep_time_min
        if self.cook_time_min is not None:
            result["cook_time_min"] = self.cook_time_min
        if self.cuisine_type is not None:
            result["cuisine_type"] = self.cuisine_type
        if self.origin_country is not None:
            result["origin_country"] = self.origin_country
        if self.emoji is not None:
            result["emoji"] = self.emoji

        return result

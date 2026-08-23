import json
import uuid
from typing import Any

from pydantic import ValidationError

from src.domain.model.ai.nutrition_contracts import (
    FoodLabelNutritionResponse,
    VisionNutritionResponse,
)
from src.domain.model.nutrition import (
    FoodItem,
    Macros,
    Nutrition,
)
from src.domain.services.emoji_validator import validate_emoji


class VisionResponseParsingError(Exception):
    """Exception raised for errors in parsing Vision AI responses."""

    pass


class VisionResponseParser:
    """
    Service for parsing Vision AI responses into domain models.

    This class implements US-2.2 - Parse the vision AI response to structured food list and macros.
    """

    MAX_FOOD_ITEMS = 8

    def __init__(self, strict_schema_mode: bool | None = None):
        if strict_schema_mode is None:
            strict_schema_mode = True
        self._strict_schema_mode = bool(strict_schema_mode)

    def parse_to_nutrition(self, gpt_response: dict[str, Any]) -> Nutrition:
        """
        Parse GPT response into Nutrition domain model.

        Args:
            gpt_response: Response from the OpenAI Vision API

        Returns:
            Nutrition object with food items and macros

        Raises:
            VisionResponseParsingError: If parsing fails due to invalid format
        """
        try:
            # Get the structured data part
            data = gpt_response.get("structured_data")
            if not data:
                raise VisionResponseParsingError(
                    "No structured data found in GPT response"
                )

            canonical = self.validate_structured_data(data)

            food_items = self._parse_food_items(canonical)
            total_macros = self._calculate_total_macros(food_items)
            confidence_score = min(max(0.0, float(canonical.confidence)), 1.0)

            # Create Nutrition object — calories derived from macros
            nutrition = Nutrition(
                macros=total_macros,
                micros=None,  # No micros from GPT
                food_items=food_items if food_items else None,
                confidence_score=confidence_score,
            )

            return nutrition

        except (KeyError, ValueError, TypeError, ValidationError) as e:
            raise VisionResponseParsingError(
                f"Failed to parse GPT response: {str(e)}"
            ) from e

    def parse_food_label_to_nutrition(self, gpt_response: dict[str, Any]) -> Nutrition:
        """Parse Nutrition Facts label output into a one-serving meal."""
        try:
            data = gpt_response.get("structured_data")
            if not data:
                raise VisionResponseParsingError(
                    "No structured data found in GPT response"
                )

            canonical = FoodLabelNutritionResponse.model_validate(dict(data))
            macros = Macros(
                protein=float(canonical.macros_per_serving.protein_g),
                carbs=float(canonical.macros_per_serving.carbs_g),
                fat=float(canonical.macros_per_serving.fat_g),
                fiber=float(canonical.macros_per_serving.fiber_g),
                sugar=float(canonical.macros_per_serving.sugar_g),
            )
            food_item = FoodItem(
                id=str(uuid.uuid4()),
                name=canonical.product_name,
                quantity=float(canonical.serving_size.grams),
                unit="g",
                macros=macros,
                micros=None,
                confidence=min(max(0.0, float(canonical.confidence)), 1.0),
                is_custom=True,
            )
            return Nutrition(
                macros=macros,
                micros=None,
                food_items=[food_item],
                confidence_score=min(max(0.0, float(canonical.confidence)), 1.0),
            )
        except (KeyError, ValueError, TypeError, ValidationError) as e:
            raise VisionResponseParsingError(
                f"Failed to parse food label response: {str(e)}"
            ) from e

    def parse_food_label_name(self, gpt_response: dict[str, Any]) -> str | None:
        """Return a display name for a scanned packaged food label."""
        try:
            structured_data = gpt_response.get("structured_data", {})
            product_name = structured_data.get("product_name")
            brand = structured_data.get("brand")
            if brand and product_name and brand.lower() not in product_name.lower():
                return f"{brand} {product_name}"
            return product_name
        except (KeyError, TypeError):
            return None

    def parse_food_label_metadata(self, gpt_response: dict[str, Any]) -> dict[str, Any]:
        """Return client-facing label metadata after schema validation."""
        try:
            structured_data = gpt_response.get("structured_data", {})
            canonical = FoodLabelNutritionResponse.model_validate(dict(structured_data))
            return canonical.model_dump()
        except (KeyError, TypeError, ValidationError, ValueError) as e:
            raise VisionResponseParsingError(
                f"Failed to validate food label metadata: {str(e)}"
            ) from e

    def validate_structured_data(self, data: dict[str, Any]) -> VisionNutritionResponse:
        """Validate structured data using the parser's canonical vision preflight."""
        try:
            normalized_data = self._normalize_structured_data(data)
            self._reject_legacy_food_item_shape(normalized_data)
            return VisionNutritionResponse.model_validate(normalized_data)
        except (KeyError, ValueError, TypeError, ValidationError) as e:
            raise VisionResponseParsingError(
                f"Failed to validate GPT response: {str(e)}"
            ) from e

    def _normalize_structured_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize structured data without repairing invalid AI food items."""
        return dict(data)

    def _reject_legacy_food_item_shape(self, data: dict[str, Any]) -> None:
        """Reject legacy parser input before contract aliases can accept it."""
        foods = data.get("foods", [])
        if foods is None:
            return
        if not isinstance(foods, list):
            raise VisionResponseParsingError("'foods' must be a list")

        for food_data in foods:
            if not isinstance(food_data, dict):
                raise VisionResponseParsingError("food item must be an object")
            if "quantity_g" not in food_data:
                raise VisionResponseParsingError(
                    "Missing required field 'quantity_g' in food item"
                )
            if "unit" in food_data:
                raise VisionResponseParsingError(
                    "Legacy field 'unit' is not allowed in canonical food item"
                )

            macros_data = food_data.get("macros")
            if not isinstance(macros_data, dict):
                raise VisionResponseParsingError(
                    "Missing required field 'macros' in food item"
                )
            for field in ("protein_g", "carbs_g", "fat_g"):
                if field not in macros_data:
                    raise VisionResponseParsingError(
                        f"Missing required field '{field}' in food item macros"
                    )

    def _parse_food_items(self, data: VisionNutritionResponse) -> list[FoodItem]:
        """Parse food items from canonical AI vision output."""
        food_items: list[FoodItem] = []

        for food_data in data.foods[: self.MAX_FOOD_ITEMS]:
            macros = Macros(
                protein=float(food_data.macros.protein_g),
                carbs=float(food_data.macros.carbs_g),
                fat=float(food_data.macros.fat_g),
                fiber=float(food_data.macros.fiber_g),
                sugar=float(food_data.macros.sugar_g),
            )

            food_items.append(
                FoodItem(
                    id=str(uuid.uuid4()),
                    name=food_data.name,
                    quantity=float(food_data.quantity_g),
                    unit="g",
                    macros=macros,
                    micros=None,
                    confidence=min(max(0.0, float(food_data.confidence)), 1.0),
                )
            )

        return food_items

    def _calculate_total_macros(self, food_items: list[FoodItem]) -> Macros:
        """Calculate total macros from canonical food items."""
        return Macros(
            protein=sum(item.macros.protein for item in food_items),
            carbs=sum(item.macros.carbs for item in food_items),
            fat=sum(item.macros.fat for item in food_items),
            fiber=sum(item.macros.fiber for item in food_items),
            sugar=sum(item.macros.sugar for item in food_items),
        )

    def parse_is_food(self, gpt_response: dict[str, Any]) -> bool:
        """Parse food-presence guard from GPT response with legacy-safe default."""
        try:
            structured_data = gpt_response.get("structured_data", {})
            if (
                not isinstance(structured_data, dict)
                or "is_food" not in structured_data
            ):
                return True

            value = structured_data.get("is_food")
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() not in {"false", "0", "no", "non_food"}
            if isinstance(value, (int, float)):
                return value != 0
            return True
        except (KeyError, TypeError):
            return True

    def parse_dish_name(self, gpt_response: dict[str, Any]) -> str | None:
        """
        Parse dish name from GPT response.

        Args:
            gpt_response: Response from the OpenAI Vision API

        Returns:
            Dish name string or None if not found
        """
        try:
            structured_data = gpt_response.get("structured_data", {})
            return structured_data.get("dish_name")
        except (KeyError, TypeError):
            return None

    def parse_emoji(self, gpt_response: dict[str, Any]) -> str | None:
        """Parse and validate emoji from AI response."""
        try:
            structured_data = gpt_response.get("structured_data", {})
            return validate_emoji(structured_data.get("emoji"))
        except (KeyError, TypeError):
            return None

    def extract_raw_json(self, gpt_response: dict[str, Any]) -> str:
        """
        Extract the raw JSON from GPT response as a string.

        Args:
            gpt_response: Response from the OpenAI Vision API

        Returns:
            JSON string representation
        """
        try:
            # If raw_response exists, we prefer that for storage
            if "raw_response" in gpt_response:
                return gpt_response["raw_response"]

            # Otherwise, just stringify the structured data
            return json.dumps(gpt_response["structured_data"])
        except (KeyError, TypeError):
            return json.dumps(gpt_response)


# Backward-compat aliases — existing callers that import the old names continue to work.
GPTResponseParser = VisionResponseParser
GPTResponseParsingError = VisionResponseParsingError

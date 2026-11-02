"""Mock Vision AI Service for testing."""

from copy import deepcopy
from typing import Any

from src.domain.ports.vision_ai_service_port import VisionAIServicePort
from src.domain.strategies.meal_analysis_strategy import MealAnalysisStrategy


class MockVisionAIService(VisionAIServicePort):
    """Mock implementation of vision AI service for testing."""

    def __init__(self, mock_response: dict[str, Any] = None):
        """Initialize with optional mock response."""
        self.mock_response = mock_response or self._default_response()

    async def analyze(
        self,
        image_bytes: bytes,
        language: str = "en",
        correction_hints: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return mock analysis result."""
        return self.mock_response

    async def analyze_by_url_with_strategy(
        self, image_url: str, strategy: MealAnalysisStrategy
    ) -> dict[str, Any]:
        """Return mock analysis result for URL-based strategy analysis."""
        response = deepcopy(self.mock_response)
        response["image_url"] = image_url
        response["strategy_used"] = strategy.get_strategy_name()
        return response

    async def analyze_with_ingredients_context(
        self, image_bytes: bytes, ingredients: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Return mock analysis result with ingredients context."""
        response = deepcopy(self.mock_response)
        if ingredients:
            response["ingredients_context"] = ingredients
        return response

    async def analyze_with_portion_context(
        self, image_bytes: bytes, portion_size: float, unit: str
    ) -> dict[str, Any]:
        """Return mock analysis result with portion context."""
        response = deepcopy(self.mock_response)
        response["portion_context"] = {"portion_size": portion_size, "unit": unit}
        return response

    async def analyze_with_weight_context(
        self, image_bytes: bytes, weight_grams: float
    ) -> dict[str, Any]:
        """Return mock analysis result with weight context."""
        response = deepcopy(self.mock_response)
        response["weight_context"] = {"weight_grams": weight_grams}
        if "structured_data" in response:
            weight_factor = weight_grams / 100.0
            for food in response["structured_data"].get("foods", []):
                food["quantity_g"] = float(food["quantity_g"]) * weight_factor
                macros = food.get("macros", {})
                for field in ("protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g"):
                    if field in macros:
                        macros[field] = float(macros[field]) * weight_factor
        return response

    async def analyze_with_strategy(
        self, image_bytes: bytes, strategy: MealAnalysisStrategy
    ) -> dict[str, Any]:
        """Return mock analysis result using a strategy."""
        response = deepcopy(self.mock_response)
        response["strategy_used"] = strategy.get_strategy_name()
        return response

    def _default_response(self) -> dict[str, Any]:
        """Default mock response for meal analysis."""
        return {
            "structured_data": {
                "dish_name": "Grilled Chicken with Rice",
                "confidence": 0.92,
                "foods": [
                    {
                        "name": "Grilled Chicken Breast",
                        "quantity_g": 150,
                        "confidence": 0.95,
                        "macros": {
                            "protein_g": 40,
                            "carbs_g": 0,
                            "fat_g": 8,
                            "fiber_g": 0,
                            "sugar_g": 0,
                        },
                    },
                    {
                        "name": "White Rice",
                        "quantity_g": 200,
                        "confidence": 0.90,
                        "macros": {
                            "protein_g": 5,
                            "carbs_g": 55,
                            "fat_g": 1,
                            "fiber_g": 0,
                            "sugar_g": 0,
                        },
                    },
                    {
                        "name": "Mixed Vegetables",
                        "quantity_g": 100,
                        "confidence": 0.88,
                        "macros": {
                            "protein_g": 3,
                            "carbs_g": 15,
                            "fat_g": 8,
                            "fiber_g": 0,
                            "sugar_g": 0,
                        },
                    },
                ],
            },
            "raw_response": "Mock AI response for testing",
        }

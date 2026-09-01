from abc import ABC, abstractmethod
from typing import Any

from src.domain.strategies.meal_analysis_strategy import MealAnalysisStrategy


class VisionAIServicePort(ABC):
    """
    Port interface for AI vision services that can analyze food images.

    This port is used by the application layer to interact with vision AI services
    behind the configured provider router.

    Meal-scan methods keep canonical food identities in English and may include
    same-call localized display fields for the requested language.
    """

    @abstractmethod
    async def analyze(
        self,
        image_bytes: bytes,
        language: str = "en",
    ) -> dict[str, Any]:
        """
        Analyze a food image to extract nutritional information.

        Args:
            image_bytes: The raw bytes of the image to analyze

        Returns:
            JSON-compatible dictionary with the raw AI response

        Raises:
            RuntimeError: If analysis fails
        """
        pass

    @abstractmethod
    async def analyze_with_strategy(
        self, image_bytes: bytes, strategy: MealAnalysisStrategy
    ) -> dict[str, Any]:
        """
        Analyze a food image using a custom analysis strategy.

        Args:
            image_bytes: The raw bytes of the image to analyze
            strategy: The analysis strategy to use

        Returns:
            JSON-compatible dictionary with the raw AI response
        """
        pass

    @abstractmethod
    async def analyze_by_url_with_strategy(
        self, image_url: str, strategy: MealAnalysisStrategy
    ) -> dict[str, Any]:
        """
        Analyze a food image URL using a custom analysis strategy.

        Args:
            image_url: Public URL of image to analyze
            strategy: The analysis strategy to use

        Returns:
            JSON-compatible dictionary with the raw AI response
        """
        pass

    @abstractmethod
    async def analyze_with_ingredients_context(
        self, image_bytes: bytes, ingredients: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Analyze a food image with ingredients context.

        Args:
            image_bytes: The raw bytes of the image to analyze
            ingredients: List of ingredient dictionaries

        Returns:
            JSON-compatible dictionary with the raw AI response

        Raises:
            RuntimeError: If analysis fails
        """
        pass

    @abstractmethod
    async def analyze_with_portion_context(
        self, image_bytes: bytes, portion_size: float, unit: str
    ) -> dict[str, Any]:
        """
        Analyze a food image with portion size context.

        Args:
            image_bytes: The raw bytes of the image to analyze
            portion_size: The target portion size
            unit: The unit of the portion size

        Returns:
            JSON-compatible dictionary with the raw AI response

        Raises:
            RuntimeError: If analysis fails
        """
        pass

    @abstractmethod
    async def analyze_with_weight_context(
        self, image_bytes: bytes, weight_grams: float
    ) -> dict[str, Any]:
        """
        Analyze a food image with specific weight context for accurate nutrition.

        Args:
            image_bytes: The raw bytes of the image to analyze
            weight_grams: The target weight in grams

        Returns:
            JSON-compatible dictionary with the raw AI response

        Raises:
            RuntimeError: If analysis fails
        """
        pass
